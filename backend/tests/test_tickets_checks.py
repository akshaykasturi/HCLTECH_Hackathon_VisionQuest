"""Step 6 checks: good cited steps pass; unsupported, unsafe or made-up steps are dropped."""
import json
from pathlib import Path

import pytest

from app.tickets.checks import build_evidence, check_recommendation
from app.tickets.models import KBHit, Recommendation, SimilarTicket, Step

FIXTURE = Path(__file__).parent / "fixtures" / "sample_evidence.json"
DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
SIMILAR = [SimilarTicket(**s) for s in DATA["similar"]]
KB = [KBHit(**k) for k in DATA["kb"]]
ALLOWED = [s.ticket_id for s in SIMILAR] + [k.section_id for k in KB]

GOOD_RESET = Step(
    text="Reset the VPN client profile, then reconnect.",
    source_id="doc:kb/vpn-troubleshooting#2",
    excerpt="Reset the VPN client profile, then check that the home router allows IPsec passthrough.",
    why="The KB article for error 809 gives this as the first fix.",
)
GOOD_ROUTER = Step(
    text="Check that the home router allows IPsec passthrough.",
    source_id="INC10432",
    excerpt="Confirmed the user's home router allows IPsec passthrough.",  # a quote may say "Confirmed"
    why="The same error 809 from home was fixed this way.",
)


def rec(*steps: Step, summary: str = "Likely a VPN profile problem; reset it and check the router.") -> Recommendation:
    return Recommendation(summary=summary, steps=list(steps), generator="gemini")


def run(r: Recommendation, allowed=None):
    return check_recommendation(r, SIMILAR, KB, ALLOWED if allowed is None else allowed)


def codes(findings):
    return {f.code for f in findings}


def test_good_steps_pass_untouched():
    checked, findings = run(rec(GOOD_RESET, GOOD_ROUTER))
    assert checked is not None
    assert [s.source_id for s in checked.steps] == ["doc:kb/vpn-troubleshooting#2", "INC10432"]
    assert checked.summary.startswith("Likely a VPN profile problem")
    assert findings == []


@pytest.mark.parametrize("bad,code", [
    (GOOD_RESET.model_copy(update={"source_id": "INC99999"}), "STEP_SOURCE_NOT_ALLOWED"),
    (GOOD_RESET.model_copy(update={"excerpt": "Reinstall Windows and reset the VPN client profile."}),
     "CITE_QUOTE_NOT_FOUND"),
    (GOOD_RESET.model_copy(update={"excerpt": "Reset"}), "CITE_QUOTE_TOO_SHORT"),
    (GOOD_RESET.model_copy(update={"text": "Reset the VPN client profile and wait 30 minutes."}),
     "NUMBER_NOT_IN_EVIDENCE"),
    (GOOD_RESET.model_copy(update={"why": "This is definitely the fix."}), "CERTAINTY"),
    (GOOD_RESET.model_copy(update={"text": "Restart the VPN server."}), "UNSAFE_STEP"),
    (GOOD_RESET.model_copy(update={"text": "Delete the user's VPN account and reset the VPN client profile."}),
     "UNSAFE_STEP"),
    (GOOD_RESET.model_copy(update={"text": "Reset the VPN client profile and mail priya@example.com."}), "SECRET"),
    (Step(text="Reset the user's directory password.", source_id="INC11205",
          excerpt="Reset the user's directory password", why="Shares the word VPN."), "STEP_SOURCE_LOOKALIKE"),
])
def test_bad_step_is_dropped_and_good_one_kept(bad, code):
    checked, findings = run(rec(bad, GOOD_ROUTER))
    assert checked is not None and checked.steps == [GOOD_ROUTER]
    assert code in codes(findings)
    assert all(f.where.startswith("steps[0]") for f in findings if f.severity == "block")


def test_number_must_come_from_the_cited_source_not_another_one():
    # 809 is in the KB article and in INC10432's subject, but not in INC10977.
    step = Step(text="Update the VPN client; error 809 goes away.", source_id="INC10977",
                excerpt="Updated the VPN client to the latest version", why="Same service.")
    checked, findings = run(rec(step, GOOD_RESET))
    assert checked.steps == [GOOD_RESET] and "NUMBER_NOT_IN_EVIDENCE" in codes(findings)


def test_everyday_user_device_fixes_are_allowed():
    restart = Step(text="Restart the laptop and install the current VPN client from the software portal.",
                   source_id="doc:kb/vpn-troubleshooting#3",
                   excerpt="uninstall the VPN client, restart the laptop and install the current version",
                   why="The KB says to reinstall if resetting the profile does not help.")
    power = Step(text="Disable power saving on the Wi-Fi adapter.", source_id="INC10977",
                 excerpt="disabled power saving on the Wi-Fi adapter", why="Fixed the same VPN drop-outs.")
    checked, findings = run(rec(restart, power))
    assert checked is not None and len(checked.steps) == 2
    assert "UNSAFE_STEP" not in codes(findings)


def test_step_from_an_unresolved_ticket_is_dropped():
    open_ticket = SIMILAR[0].model_copy(update={"status": "In Progress"})
    checked, findings = check_recommendation(rec(GOOD_ROUTER, GOOD_RESET), [open_ticket, *SIMILAR[1:]], KB, ALLOWED)
    assert checked.steps == [GOOD_RESET] and "STEP_SOURCE_NOT_RESOLVED" in codes(findings)


def test_allowed_ids_are_respected_even_if_the_source_exists():
    checked, findings = run(rec(GOOD_ROUTER, GOOD_RESET), allowed=["doc:kb/vpn-troubleshooting#2"])
    assert checked.steps == [GOOD_RESET] and "STEP_SOURCE_NOT_ALLOWED" in codes(findings)


def test_duplicate_step_is_dropped():
    checked, findings = run(rec(GOOD_RESET, GOOD_RESET.model_copy(update={"text": "  reset the VPN client profile, then RECONNECT. "})))
    assert len(checked.steps) == 1 and "STEP_DUPLICATE" in codes(findings)


def test_no_steps_left_returns_none_so_the_pipeline_escalates():
    checked, findings = run(rec(GOOD_RESET.model_copy(update={"source_id": "INC99999"})))
    assert checked is None and "NO_STEPS_LEFT" in codes(findings)


def test_empty_recommendation_returns_none():
    checked, findings = run(rec())
    assert checked is None and codes(findings) == {"NO_STEPS_LEFT"}


@pytest.mark.parametrize("summary,code", [
    ("The root cause is confirmed: a broken VPN profile.", "CERTAINTY"),
    ("Seen 42 times this month; reset the profile.", "NUMBER_NOT_IN_EVIDENCE"),
    ("Ask priya@example.com to reset the profile.", "SECRET"),
])
def test_bad_summary_is_replaced_steps_are_kept(summary, code):
    checked, findings = run(rec(GOOD_RESET, summary=summary))
    assert checked.summary.startswith("Suggested steps") and checked.steps == [GOOD_RESET]
    assert code in codes(findings)


def test_negated_certainty_is_fine():
    checked, findings = run(rec(GOOD_RESET, summary="The cause is not confirmed yet; error 809 points to the profile."))
    assert checked.summary.startswith("The cause is not confirmed") and findings == []


def test_unrelated_step_text_is_a_warning_not_a_drop():
    step = GOOD_RESET.model_copy(update={"text": "Try again."})
    checked, findings = run(rec(step))
    assert checked.steps == [step]
    assert [(f.code, f.severity) for f in findings] == [("STEP_TEXT_UNRELATED", "warn")]


def test_input_recommendation_is_not_changed():
    original = rec(GOOD_RESET.model_copy(update={"source_id": "INC99999"}), GOOD_ROUTER)
    run(original)
    assert len(original.steps) == 2


def test_evidence_holds_subject_and_resolution_and_kb_title():
    by_id = {e.id: e.text for e in build_evidence(SIMILAR, KB)}
    assert "VPN error 809 from home network" in by_id["INC10432"]
    assert "Reset the VPN client profile" in by_id["INC10432"]
    assert by_id["doc:kb/vpn-troubleshooting#2"].startswith("VPN error 809")
