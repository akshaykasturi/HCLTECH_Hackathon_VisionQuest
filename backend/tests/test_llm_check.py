"""scripts/llm_check.py with a fake LLM: counting kept and dropped steps, and the report. No real API calls."""
import importlib.util
from pathlib import Path

import pytest

from app.tickets.generate import GenerateResult
from app.tickets.models import Recommendation, Step

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "llm_check.py"
spec = importlib.util.spec_from_file_location("llm_check", SCRIPT)
llm_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(llm_check)

needs_data = pytest.mark.skipif(not llm_check.TICKETS_CSV.is_file() or not llm_check.LABELLED_FILE.is_file(),
                                reason="team data not present")


def copying_llm(ticket, classification, similar, kb, allowed_ids):
    """A well-behaved fake LLM: one step copied word for word from the first allowed past ticket, plus one made-up
    step citing a source that is not allowed (the checks must drop it)."""
    source = next((s for s in similar if s.ticket_id in allowed_ids and s.resolution_text), None)
    if source is None:
        return GenerateResult(Recommendation(summary="Nothing fits.", steps=[], generator="fake"), "fake", [])
    piece = source.resolution_text.split("Steps:")[-1].split(" | ")[0].strip()
    good = Step(text=piece, source_id=source.ticket_id, excerpt=piece, why="Same issue, resolved this way.")
    bad = Step(text="Restart the core router.", source_id="INC-NOT-REAL", excerpt="restart the core router now",
               why="It is definitely the router.")
    return GenerateResult(Recommendation(summary="Likely the same issue as a past ticket.", steps=[good, bad],
                                         generator="fake"), "fake", [])


def silent_llm(*args):
    return GenerateResult(None, None, ["gemini: skipped (GEMINI_API_KEY not set)"])


@needs_data
def test_kept_and_dropped_steps_are_counted():
    cases = llm_check.load_tickets("val", 3)
    assert len(cases) == 3
    results = [llm_check.check_one(c, copying_llm) for c in cases]
    s = llm_check.summarise(results)
    assert s["answered"] == 3 and s["providers"] == {"fake": 3}
    assert s["steps_written"] == 6 and s["steps_kept"] == 3 and s["step_groundedness"] == 0.5
    assert s["tickets_verified"] == 3
    assert s["why_dropped"]["STEP_SOURCE_NOT_ALLOWED"] == 3
    assert results[0]["dropped_examples"][0]["source_id"] == "INC-NOT-REAL"


@needs_data
def test_no_llm_means_nothing_answered():
    results = [llm_check.check_one(c, silent_llm) for c in llm_check.load_tickets("val", 2)]
    s = llm_check.summarise(results)
    assert s["answered"] == 0 and s["steps_written"] == 0 and s["step_groundedness"] is None
    assert s["llm_errors"] == ["gemini: skipped (GEMINI_API_KEY not set)"]


@needs_data
def test_own_ticket_is_never_its_own_evidence():
    from app.tickets.guard import mask_ticket
    from app.tickets.models import TicketIn
    case = llm_check.load_tickets("val", 1)[0]
    r = case["row"]
    masked, _ = mask_ticket(TicketIn(subject=r["subject"], description=r["description"]))
    similar, _, allowed = llm_check.evidence_for(masked, case["ticket_id"])
    assert case["ticket_id"] not in {s.ticket_id for s in similar} and case["ticket_id"] not in allowed


@needs_data
def test_main_runs_end_to_end_with_a_fake_llm(capsys):
    assert llm_check.main(["--n", "2", "--pause", "0"], generate_fn=copying_llm) == 0
    out = capsys.readouterr().out
    assert "answered 2/2" in out and "steps kept 2/4 (50%)" in out


def test_report_has_the_numbers():
    results = [{"case_id": "E1", "ticket_id": "INC-1", "evidence": 4, "provider": "gemini", "ms": 900,
                "llm_errors": [], "written": 3, "kept": 2, "codes": ["CITE_QUOTE_NOT_FOUND"], "warnings": [],
                "dropped_examples": [{"codes": ["CITE_QUOTE_NOT_FOUND"], "text": "t", "source_id": "INC-9",
                                      "excerpt": "e"}]}]
    md = llm_check.render_markdown(llm_check.summarise(results), results, "val", "2026-11-17 09:00 UTC")
    assert "| Steps kept / written (step groundedness) | 2/3 (67%) |" in md
    assert "| CITE_QUOTE_NOT_FOUND | 1 |" in md and "INC-9" in md
