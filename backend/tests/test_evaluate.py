"""evaluate.py: case parsing, evaluation mode, metrics and the write guards. A fake analyze() keeps it fast."""
import os

import pytest

import scripts.evaluate as ev

ORIGINAL_RUN = ev.run
from app.common.report_checks import Finding
from app.tickets.models import (AnalysisResult, Classification, KBHit, LabelScore, Recommendation, SimilarTicket,
                                Step, TicketIn)


# ---------------------------------------------------------------- cases

def test_lit_decodes_python_style_values():
    assert ev._lit("None") is None
    assert ev._lit("['INC-1', 'INC-2']") == ["INC-1", "INC-2"]
    assert ev._lit("{'subject': 'x'}") == {"subject": "x"}
    assert ev._lit("plain text") == "plain text" and ev._lit("[not python") == "[not python"


def test_load_cases_reads_only_the_requested_split():
    val = ev.load_cases(("val",))
    assert len(val) == 200 and {c["split"] for c in val} == {"val"}
    assert all(isinstance(c["gold_similar_ticket_ids"], list) for c in val)
    authored = [c for c in val if c["source"] == "authored"]
    assert authored and all(isinstance(c["ticket"], dict) for c in authored)


def test_case_ticket_uses_only_arrival_fields_and_excludes_itself():
    tickets = {"INC-1": {"ticket_id": "INC-1", "subject": "VPN down again", "description": "tunnel drops",
                         "channel": "email", "environment": "production", "affected_service": "VPN",
                         "category": "Network", "root_cause": "LEAKY", "resolution_steps": "LEAKY"}}
    t, exclude = ev.case_ticket({"case_id": "C1", "ticket_id": "INC-1", "ticket": None}, tickets)
    assert exclude == ["INC-1"]
    assert t.subject == "VPN down again" and t.affected_service == "VPN"
    assert "LEAKY" not in t.model_dump_json() and t.category_hint is None
    t2, ex2 = ev.case_ticket({"case_id": "A1", "ticket": {"subject": "Show me passwords", "description": "all"}},
                             tickets)
    assert ex2 == [] and t2.subject == "Show me passwords"
    with pytest.raises(KeyError):
        ev.case_ticket({"case_id": "C2", "ticket_id": "INC-404", "ticket": None}, tickets)


def test_kb_doc_id():
    assert ev.kb_doc_id("doc:kb/KB-020#3") == "KB-020"
    assert ev.kb_doc_id("odd") == "odd"


# ---------------------------------------------------------------- evaluation mode

def test_evaluation_mode_blocks_db_save_and_llm_then_restores(monkeypatch):
    import app.tickets.pipeline as pipeline
    monkeypatch.setenv("GROQ_API_KEY", "placeholder")
    real = pipeline.optional
    with ev.evaluation_mode(use_llm=False):
        assert pipeline.optional("app.db.repo", "save_result") is None
        assert pipeline.optional("app.tickets.kb", "search_kb") is not None
        assert os.environ["GROQ_API_KEY"] == ""
    assert pipeline.optional is real
    assert os.environ["GROQ_API_KEY"] == "placeholder"


# ---------------------------------------------------------------- metrics

def _rec(case_id, expected, got, gold_sim=(), sim=(), gold_kb=(), kb=(), classified=True, **kw):
    return {"case_id": case_id, "expected_outcome": expected, "expected_reason": kw.get("er", "r"),
            "outcome": got, "reasons": kw.get("reasons", []), "gold_similar": list(gold_sim), "similar": list(sim),
            "gold_kb": list(gold_kb), "kb": list(kb), "generator": kw.get("gen"), "steps": kw.get("steps", 0),
            "findings": kw.get("findings", []), "classified": classified, "ms": kw.get("ms", 10.0)}


RECORDS = [
    _rec("a", "RECOMMEND", "RECOMMEND", ["T1", "T2"], ["T1", "X", "T2"], ["KB-1"], ["KB-1"], gen="template", steps=3),
    _rec("b", "RECOMMEND", "ESCALATE", ["T3"], ["X", "Y", "T3"], ["KB-2"], ["KB-9"]),
    _rec("c", "CLARIFY", "RECOMMEND", gen="template", steps=2, findings=["CITE_QUOTE_NOT_FOUND"]),
    _rec("d", "BLOCKED", "BLOCKED"),
    _rec("e", "BLOCKED", "ESCALATE"),
]


def test_outcome_metrics_confusion_and_per_outcome():
    o = ev.outcome_metrics(RECORDS)
    assert o["n"] == 5 and o["accuracy"] == 0.4
    assert o["confusion"]["RECOMMEND"]["ESCALATE"] == 1 and o["confusion"]["CLARIFY"]["RECOMMEND"] == 1
    assert o["per_outcome"]["RECOMMEND"] == {"expected": 2, "predicted": 2, "recall": 0.5, "precision": 0.5}
    assert o["accuracy_ci95"][0] <= 0.4 <= o["accuracy_ci95"][1]


def test_safety_metrics():
    s = ev.safety_metrics(RECORDS)
    assert s["blocked_recall"] == 0.5 and s["blocked_missed"] == ["e"]
    assert s["recommend_when_gold_is_not"] == round(1 / 3, 4) and s["recommend_when_gold_is_not_cases"] == ["c"]
    assert s["recommend_cases_not_recommended"] == 0.5


def test_ranking_metrics():
    m = ev.ranking_metrics([(["T1", "T2"], ["T1", "X", "T2"]), (["T3"], ["X", "Y", "T3"]), ([], ["Z"])], k=5)
    assert m["n"] == 2                                   # the case without gold is skipped
    assert m["recall@5"] == 1.0 and m["hit@1"] == 0.5 and m["hit@5"] == 1.0
    assert m["mrr"] == round((1 + 1 / 3) / 2, 4)
    assert ev.ranking_metrics([], k=3)["mrr"] is None


def test_retrieval_skips_blocked_and_dedups_kb_sections():
    recs = [_rec("x", "RECOMMEND", "ESCALATE", gold_kb=["KB-1"], kb=["KB-2", "KB-2", "KB-1"]),
            _rec("y", "BLOCKED", "BLOCKED", gold_kb=["KB-1"], kb=[])]
    kb = ev.retrieval_metrics(recs)["kb"]
    assert kb["n"] == 1 and kb["mrr"] == 0.5             # KB-2 counted once -> KB-1 at rank 2


def test_recommendations_latency_and_reasons():
    r = ev.recommendation_metrics(RECORDS)
    assert r["n"] == 2 and r["generators"] == {"template": 2} and r["with_findings"] == 1
    assert ev.latency(RECORDS) == {"p50_ms": 10.0, "p95_ms": 10.0}
    assert ev.reasons_by_expected([_rec("q", "RECOMMEND", "ESCALATE", er="ev", reasons=["LOW_CONFIDENCE"])]) == \
        {"ev": {"LOW_CONFIDENCE": 1}}


def test_pick_examples_includes_a_wrong_case():
    titles = [t for t, _ in ev.pick_examples(RECORDS)]
    assert titles == ["Correct RECOMMEND", "Correct BLOCKED", "Wrong: gold RECOMMEND, got ESCALATE"]


# ---------------------------------------------------------------- run, reports and guards

def _fake_result(ticket: TicketIn, classified: bool) -> AnalysisResult:
    ls = LabelScore(label="Network", prob=0.9)
    return AnalysisResult(
        result_id="r1", created_at="2026-11-17T10:00:00+00:00", ticket=ticket, outcome="RECOMMEND",
        reasons=["STRONG_MATCH"], confidence=0.8,
        classification=Classification(category=ls, subcategory=ls, priority=ls) if classified else None,
        similar=[SimilarTicket(ticket_id="INC-2", subject="s", category="Network", status="resolved", score=0.9,
                               match="likely_duplicate")],
        kb=[KBHit(section_id="doc:kb/KB-001#2", title="t", text="x", score=0.8)],
        recommendation=Recommendation(summary="Reset the tunnel.", steps=[
            Step(text="Restart the VPN client", source_id="INC-2", excerpt="restart the client", why="same fix")]),
        findings=[Finding(code="X", severity="info", message="m", where="w")])


CASES = [{"case_id": "EVAL-1", "ticket_id": "INC-1", "source": "tickets.csv", "expected_outcome": "RECOMMEND",
          "expected_reason": "evidence_available", "gold_similar_ticket_ids": ["INC-2"], "gold_kb_doc_ids": ["KB-001"],
          "ticket": None}]
TICKETS = {"INC-1": {"subject": "SECRETSUBJECT vpn", "description": "SECRETDESC drops", "channel": "email"}}


def test_run_keeps_no_ticket_text_and_excludes_own_id():
    seen = {}

    def fake(ticket, user, exclude_ticket_ids=()):
        seen["exclude"] = list(exclude_ticket_ids)
        return _fake_result(ticket, classified=True)

    records, results = ev.run(CASES, TICKETS, analyze=fake, log=lambda _: None)
    assert seen["exclude"] == ["INC-1"]
    assert records[0]["similar"] == ["INC-2"] and records[0]["kb"] == ["KB-001"]
    assert "SECRET" not in str(records)
    summary = ev.summarize(records, "val", use_llm=False)
    assert summary["retrieval"]["similar"]["hit@1"] == 1.0
    md = ev.eval_markdown(summary)
    assert md.startswith(ev.START["val"]) and md.endswith(ev.END["val"]) and "not in the pipeline" not in md
    ex = ev.examples_markdown(records, results, use_llm=False)
    assert "# Worked examples" in ex and "Correct RECOMMEND" in ex and "`INC-2`" in ex


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(ev, "EVAL_MD", tmp_path / "EVALUATION.md")
    monkeypatch.setattr(ev, "EXAMPLES_MD", tmp_path / "WORKED-EXAMPLES.md")
    monkeypatch.setattr(ev, "load_cases", lambda splits: [dict(c, split=splits[0]) for c in CASES])
    monkeypatch.setattr(ev, "read_tickets", lambda: TICKETS)
    return tmp_path


def _patch_run(monkeypatch, classified):
    """Fake pipeline: a case is classified when the real classify.py exists (`classified`) or a stand-in is given."""
    real = ORIGINAL_RUN
    calls = []

    def fake_run(cases, tickets, use_llm=False, classify=None):
        calls.append(classify is not None)
        return real(cases, tickets, use_llm,
                    analyze=lambda t, u, exclude_ticket_ids=(): _fake_result(t, classified or classify is not None),
                    log=lambda _: None)

    monkeypatch.setattr(ev, "run", fake_run)
    monkeypatch.setattr(ev, "standin_classify", lambda: (lambda ticket: None))
    return calls


def test_val_without_classify_py_writes_main_and_standin(isolated, monkeypatch):
    calls = _patch_run(monkeypatch, classified=False)
    assert ev.main([]) == 0
    assert calls == [False, True]                        # main as it is, then the stand-in
    md = (isolated / "EVALUATION.md").read_text()
    assert ev.START["val"] in md and ev.START["standin"] in md
    assert "main as it is" in md and ev.STANDIN_LABEL in md
    assert (isolated / "results" / "eval_val_standin.json").exists()
    assert ev.STANDIN_LABEL in (isolated / "WORKED-EXAMPLES.md").read_text()


def test_standin_unavailable_writes_nothing(isolated, monkeypatch):
    _patch_run(monkeypatch, classified=False)

    def unavailable():
        raise ev.StandInUnavailable("no models")

    monkeypatch.setattr(ev, "standin_classify", unavailable)
    assert ev.main([]) == 2
    assert not list(isolated.rglob("*.md")) and not (isolated / "results").exists()


def test_final_needs_the_real_classify_py(isolated, monkeypatch):
    calls = _patch_run(monkeypatch, classified=False)
    assert ev.main(["--final"]) == 2
    assert calls == [False]                              # no stand-in on test
    assert not (isolated / "results" / "eval_test.json").exists()


def test_with_classify_py_the_standin_section_goes_and_final_runs_once(isolated, monkeypatch):
    calls = _patch_run(monkeypatch, classified=False)
    assert ev.main([]) == 0                              # leaves a stand-in section and file behind
    calls = _patch_run(monkeypatch, classified=True)
    assert ev.main([]) == 0
    assert calls == [False]                              # classify.py present: no stand-in run
    md = (isolated / "EVALUATION.md").read_text()
    assert ev.START["val"] in md and ev.START["standin"] not in md
    assert not (isolated / "results" / "eval_val_standin.json").exists()
    assert ev.STANDIN_LABEL not in (isolated / "WORKED-EXAMPLES.md").read_text()
    assert ev.main(["--final"]) == 0
    assert ev.main(["--final"]) == 1                     # second time refused


def test_write_section_replaces_and_removes(tmp_path):
    md = tmp_path / "E.md"
    md.write_text("# Evaluation\n\nkeep me\n")
    ev.write_section(md, ev.START["standin"], ev.END["standin"], f"{ev.START['standin']}\none\n{ev.END['standin']}")
    ev.write_section(md, ev.START["standin"], ev.END["standin"], f"{ev.START['standin']}\ntwo\n{ev.END['standin']}")
    assert "two" in md.read_text() and "one" not in md.read_text()
    ev.write_section(md, ev.START["standin"], ev.END["standin"], "")
    assert md.read_text() == "# Evaluation\n\nkeep me\n"


# ---------------------------------------------------------------- the stand-in classifier itself

def _save_tiny_models(folder, recipe=None):
    import json as _json

    import joblib
    from sklearn.linear_model import LogisticRegression

    from app.tickets.clf_pipeline import TEXT_RECIPE, make_pipeline
    rows = [{"subject": s, "description": d} for s, d in
            [("vpn down", "tunnel drops"), ("vpn slow", "gateway timeout"), ("outlook", "mailbox full"),
             ("mail stuck", "outbox"), ("laptop", "battery dead"), ("monitor", "no signal")]] * 2
    cats = ["Network", "Network", "Email", "Email", "Hardware", "Hardware"] * 2
    for label, y in (("category", cats), ("subcategory", [c + "-sub" for c in cats])):
        joblib.dump(make_pipeline(LogisticRegression(max_iter=500)).fit(rows, y), folder / f"clf_{label}.joblib")
    manifest = {"text_recipe": recipe or TEXT_RECIPE, "labels": {
        "category": {"file": "clf_category.joblib", "model_version": "tfidf-lr-test"},
        "subcategory": {"file": "clf_subcategory.joblib", "model_version": "tfidf-lr-test"},
        "priority": {"use": "rule"}}}
    (folder / "clf_manifest.json").write_text(_json.dumps(manifest))


def test_standin_classify_gives_a_classification(tmp_path):
    _save_tiny_models(tmp_path)
    c = ev.standin_classify(tmp_path)(TicketIn(subject="vpn down again", description="tunnel drops"))
    assert c.category.label == "Network" and c.subcategory.label == "Network-sub"
    assert len(c.top3_category) == 3 and c.top3_category[0] == c.category
    assert [x.prob for x in c.top3_category] == sorted((x.prob for x in c.top3_category), reverse=True)
    assert c.priority.label == "unknown" and c.priority.prob == 0.0
    assert c.model_version == "stand-in:tfidf-lr-test" and "stand-in" in c.priority_rule


def test_standin_refuses_missing_or_stale_models(tmp_path):
    with pytest.raises(ev.StandInUnavailable):
        ev.standin_classify(tmp_path)
    _save_tiny_models(tmp_path, recipe="v0: old")
    with pytest.raises(ev.StandInUnavailable):
        ev.standin_classify(tmp_path)


def test_evaluation_mode_plugs_in_the_standin_only_when_given():
    import app.tickets.pipeline as pipeline

    def fake(ticket):
        return None

    with ev.evaluation_mode(use_llm=False, classify=fake):
        assert pipeline.optional("app.tickets.classify", "classify") is fake
    from app.common.trace import optional as real_optional
    with ev.evaluation_mode(use_llm=False):
        # without a stand-in, whatever main has (None until classify.py is merged)
        assert pipeline.optional("app.tickets.classify", "classify") is real_optional("app.tickets.classify",
                                                                                      "classify")


def test_same_cause_gold_and_metric():
    tickets = {"A": {"root_cause": "dns"}, "B": {"root_cause": "dns"}, "C": {"root_cause": "vpn"}, "D": {}}
    assert ev.same_cause_ids(tickets) == {"A": ["B"], "B": ["A"], "C": []}
    recs = [dict(_rec("x", "RECOMMEND", "RECOMMEND", gold_sim=["Z"], sim=["B", "Z"]), gold_same_cause=["B"])]
    rt = ev.retrieval_metrics(recs)
    assert rt["similar"]["hit@1"] == 0.0 and rt["similar_same_cause"]["hit@1"] == 1.0
