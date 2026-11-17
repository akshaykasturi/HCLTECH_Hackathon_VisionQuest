"""The shared shapes load, validate and reject bad input; the fixture matches them."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.tickets.models import (AnalysisResult, Classification, FeedbackIn, KBHit, SimilarTicket,
                                TicketIn)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_evidence.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_matches_shapes():
    data = load_fixture()
    TicketIn(**data["ticket"])
    Classification(**data["classification"])
    similar = [SimilarTicket(**s) for s in data["similar"]]
    kb = [KBHit(**k) for k in data["kb"]]
    assert {s.match for s in similar} == {"likely_duplicate", "related", "weak"}
    assert any(s.lookalike for s in similar)
    assert len(kb) == 2


def test_ticket_rejects_empty_subject():
    with pytest.raises(ValidationError):
        TicketIn(subject="", description="x")


def test_feedback_verdicts_are_the_briefs_words():
    assert FeedbackIn(verdict="useful").target == "result"
    with pytest.raises(ValidationError):
        FeedbackIn(verdict="confirm")


def test_result_defaults_and_blocked_outcome():
    data = load_fixture()
    r = AnalysisResult(result_id="r1", created_at="2026-11-17T12:00:00",
                       ticket=TicketIn(**data["ticket"]), outcome="BLOCKED", reasons=["INJECTION_PATTERN"])
    assert r.recommendation is None and r.similar == [] and r.confidence == 0.0
    assert AnalysisResult.model_validate_json(r.model_dump_json()) == r
