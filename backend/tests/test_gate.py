"""The code gate (app/tickets/gate.py): one test per rule a-j, then rule order, confidence and policy.
Inputs come from tests/fixtures/sample_evidence.json with one thing changed per test."""
import json
from pathlib import Path

import pytest

from app.tickets import gate
from app.tickets.gate import decide, gate_policy
from app.tickets.models import Classification, KBHit, ScreenResult, SimilarTicket, TicketIn

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sample_evidence.json").read_text(encoding="utf-8"))
ACCESS_NETWORK_Q = "Are you trying to sign in to your account or laptop, or to connect to the VPN or network?"


def ticket(**change) -> TicketIn:
    return TicketIn(**{**FIXTURE["ticket"], **change})


def classification(label="Network", prob=0.88, top3=None) -> Classification:
    data = dict(FIXTURE["classification"])
    data["category"] = {"label": label, "prob": prob}
    if top3 is not None:
        data["top3_category"] = [{"label": a, "prob": b} for a, b in top3]
    return Classification(**data)


def similar(**changes_by_id) -> list[SimilarTicket]:
    """The fixture's 3 similar tickets; changes_by_id={"INC10432": {"score": 0.9}} edits one of them."""
    return [SimilarTicket(**{**s, **changes_by_id.get(s["ticket_id"], {})}) for s in FIXTURE["similar"]]


KB = [KBHit(**k) for k in FIXTURE["kb"]]
CLEAN = ScreenResult()


DATA_TEAMS = {"Network": "Network Operations", "Identity & Access": "IAM Engineering"}  # as the pipeline passes


def run(t=None, screen=CLEAN, cls="default", sim=None, policy=None):
    return decide(t or ticket(), screen, classification() if cls == "default" else cls,
                  similar() if sim is None else sim, KB,
                  policy={"data_escalation_groups": DATA_TEAMS, **(policy or {})})


# ---- One test per rule ------------------------------------------------------------------------

def test_a_blocked():
    d = run(screen=ScreenResult(blocked=True, codes=["INJECTION_PATTERN"]))
    assert (d.outcome, d.reasons, d.confidence) == ("BLOCKED", ["INJECTION_PATTERN"], 0.0)


def test_b_safety_escalates_with_the_screen_codes_as_reasons():
    d = run(screen=ScreenResult(safety=True, codes=["SAFETY_SMOKE", "SAFETY_BURNING"]))
    assert (d.outcome, d.reasons, d.escalation_group) == \
        ("ESCALATE", ["SAFETY_SMOKE", "SAFETY_BURNING"], "Desktop Support L2 (safety)")
    for code in ("PRIVILEGED_REQUEST", "REGULATED_LEGAL", "REPEATED_FAILURE"):  # a person must decide
        assert run(screen=ScreenResult(safety=True, codes=[code])).reasons == [code]
    assert run(screen=ScreenResult(safety=True)).reasons == ["SAFETY"]  # no code given: the generic reason


def test_c_counts_unique_words_so_a_repeated_subject_is_vague():
    assert gate.unique_words("Partner API errors | Partner API errors.") == 3
    assert gate.unique_words("VPN won't connect, error 809!") == 6      # won't -> won + t; punctuation stripped
    assert gate.unique_words("Error ERROR error_809") == 2             # case-insensitive; _ splits words
    d = run(t=ticket(subject="Partner API errors", description="Partner API errors."))
    assert (d.outcome, d.reasons) == ("CLARIFY", ["TOO_VAGUE"])  # 6 words before, but only 3 distinct
    d = run(t=ticket(subject="Partner API errors", description="Partner API returns 429 since 09:00."))
    assert d.reasons != ["TOO_VAGUE"]  # new detail: enough distinct words


def test_c_too_vague_asks_for_details():
    d = run(t=ticket(subject="VPN", description="broken"))
    assert (d.outcome, d.reasons) == ("CLARIFY", ["TOO_VAGUE"])
    assert d.question == gate_policy()["vague_question"]


def test_d_no_classifier_and_no_strong_match():
    d = run(cls=None, sim=[s for s in similar() if s.ticket_id != "INC10432"])
    assert (d.outcome, d.reasons, d.escalation_group) == ("ESCALATE", ["NO_EVIDENCE"], "Service Desk L2")


@pytest.mark.parametrize("sim", [[], similar(INC10432={"score": 0.5})])
def test_e_nothing_similar(sim):
    d = run(sim=sim)
    assert (d.outcome, d.reasons, d.escalation_group) == ("ESCALATE", ["NOTHING_SIMILAR"], "Network Operations")


def test_f_two_likely_issues_uses_the_policy_question_or_the_template():
    d = run(cls=classification(prob=0.48, top3=[("Network", 0.48), ("Identity & Access", 0.42), ("Cloud", 0.1)]))
    assert (d.outcome, d.reasons, d.question) == ("CLARIFY", ["TWO_LIKELY_ISSUES"], ACCESS_NETWORK_Q)
    d = run(cls=classification(label="Software", prob=0.45, top3=[("Software", 0.45), ("Hardware", 0.40)]))
    assert d.question.startswith("Is this mainly about Hardware or Software?")


def test_g_sources_disagree():
    d = run(cls=classification(label="Identity & Access", prob=0.9,
                               top3=[("Identity & Access", 0.9), ("Network", 0.05)]))
    assert (d.outcome, d.reasons, d.question) == ("CLARIFY", ["SOURCES_DISAGREE"], ACCESS_NETWORK_Q)


def test_h_fixes_conflict_and_same_cause_does_not():
    sim = similar(INC10432={"root_cause": "Corrupt VPN profile"},
                  INC10977={"score": 0.79, "root_cause": "Outdated VPN client"})
    d = run(sim=sim)
    assert (d.outcome, d.reasons, d.escalation_group) == ("ESCALATE", ["FIXES_CONFLICT"], "Network Operations")
    same = similar(INC10432={"root_cause": "Corrupt VPN profile"},
                   INC10977={"score": 0.79, "root_cause": "  corrupt  vpn PROFILE "})
    assert run(sim=same).outcome == "RECOMMEND"


def test_i_recommend_duplicate_or_strong_match():
    d = run()
    assert (d.outcome, d.reasons, d.confidence) == ("RECOMMEND", ["DUPLICATE_FOUND"], 0.81)
    assert d.question is None and d.escalation_group is None
    assert run(sim=similar(INC10432={"match": "related"})).reasons == ["STRONG_MATCH"]


def test_j_low_confidence():
    d = run(cls=classification(prob=0.5, top3=[("Network", 0.5), ("Access", 0.2)]))
    assert (d.outcome, d.reasons, d.escalation_group) == ("ESCALATE", ["LOW_CONFIDENCE"], "Network Operations")


# ---- Order, confidence, policy, lookalikes ----------------------------------------------------

def test_blocked_beats_safety_beats_a_strong_match():
    assert run(screen=ScreenResult(blocked=True, safety=True, codes=["JAILBREAK"])).outcome == "BLOCKED"
    assert run(screen=ScreenResult(safety=True)).reasons == ["SAFETY"]  # the evidence would have recommended


def test_vague_beats_every_rule_after_it():
    d = run(t=ticket(subject="help", description="pls"), cls=None, sim=[])  # d and e would also fire
    assert d.reasons == ["TOO_VAGUE"]


def test_confidence_is_the_minimum():
    assert run().confidence == 0.81                                            # min(0.88 class, 0.81 strong)
    assert run(cls=classification(prob=0.7)).confidence == 0.7                 # min(0.70 class, 0.81 strong)
    only_weak = [SimilarTicket(**{**FIXTURE["similar"][1], "score": 0.6})]
    d = run(sim=only_weak)                                                     # no strong: best score counts
    assert (d.outcome, d.confidence) == ("ESCALATE", 0.6)
    assert run(cls=None, sim=[]).confidence == 0.0


def test_policy_override_changes_the_outcome():
    assert run(policy={"min_class_prob": 0.95}).reasons == ["LOW_CONFIDENCE"]
    assert run(policy={"min_words": 50}).reasons == ["TOO_VAGUE"]


def test_lookalikes_never_count_as_strong():
    lookalike = [s for s in similar(INC11205={"score": 0.95}) if s.ticket_id == "INC11205"]
    assert lookalike[0].lookalike
    assert run(sim=lookalike).reasons == ["LOW_CONFIDENCE"]       # high score, but not evidence
    assert run(cls=None, sim=lookalike).reasons == ["NO_EVIDENCE"]


def test_policy_file_is_read():
    p = gate_policy()
    assert p["escalation_groups"] == {}  # teams come from the data unless overridden on purpose
    assert p["clarify_questions"]["Identity & Access|Network"] == ACCESS_NETWORK_Q
    assert p["related_threshold"] == 0.70 and gate.DEFAULTS["min_words"] == p["min_words"]


def test_escalation_group_comes_from_data_then_policy_override_then_default():
    low = classification(prob=0.5, top3=[("Network", 0.5), ("Cloud", 0.2)])
    assert run(cls=low).escalation_group == "Network Operations"                       # data
    override = {"escalation_groups": {"Network": "NOC night shift"}}
    assert run(cls=low, policy=override).escalation_group == "NOC night shift"        # policy wins
    unknown = classification(label="Quantum Lab", prob=0.5, top3=[("Quantum Lab", 0.5), ("Cloud", 0.2)])
    only_weak = [SimilarTicket(**{**FIXTURE["similar"][1], "score": 0.6})]          # no strong match to disagree
    assert run(cls=unknown, sim=only_weak).escalation_group == "Service Desk L2"      # default
    assert gate.escalation_group_for(None, {**gate_policy(), "data_escalation_groups": DATA_TEAMS}) \
        == "Service Desk L2"
