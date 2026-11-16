"""Report checks (app/common/report_checks.py): code checks the LLM's output before anyone sees it."""
import pytest
from pydantic import BaseModel

from app.common.report_checks import (
    Citation, Evidence, check_certainty, check_citations, check_numbers, check_safe_steps, check_secrets,
    groundedness, has_blocks,
)

EVIDENCE = [
    Evidence(id="metric:latency", text="p95 latency 2,400 ms vs 180 ms in the hour before (x13.3).",
             data={"p95_ms": 2400.0, "baseline_ms": 180.0, "error_rate": 0.125}),
    Evidence(id="log:window", text="17 of 40 requests failed between 05:10 and 05:20 on 2026-10-15.",
             data={"failed": 17, "total": 40, "status_counts": {"504": 17}}),
]


def codes(findings) -> list[str]:
    return [f.code for f in findings]


# ---------- citations ----------

def test_good_citations_pass_and_groundedness_is_full() -> None:
    cites = [Citation(evidence_id="metric:latency", quote="p95 latency 2,400 ms"),
             Citation(evidence_id="log:window", quote="17 of 40 requests failed")]
    assert check_citations([(f"c[{i}]", c) for i, c in enumerate(cites)], EVIDENCE) == []
    assert groundedness(cites, EVIDENCE) == 1.0


@pytest.mark.parametrize("citation, code", [
    (Citation(evidence_id="metric:missing", quote="p95 latency 2,400 ms"), "CITE_UNKNOWN_ID"),
    (Citation(evidence_id="metric:latency", quote="p95 latency 9,999 ms"), "CITE_QUOTE_NOT_FOUND"),
    (Citation(evidence_id="metric:latency", quote="ms"), "CITE_QUOTE_TOO_SHORT"),
])
def test_bad_citations_are_blocked(citation, code) -> None:
    findings = check_citations([("items[0].supporting[0]", citation)], EVIDENCE)
    assert codes(findings) == [code] and findings[0].where == "items[0].supporting[0]"
    assert has_blocks(findings)


def test_quote_ignores_case_and_extra_spaces() -> None:
    c = Citation(evidence_id="metric:latency", quote="P95   LATENCY 2,400 ms")
    assert check_citations([("x", c)], EVIDENCE) == []


def test_groundedness_counts_the_share_that_passes() -> None:
    cites = [Citation(evidence_id="metric:latency", quote="p95 latency 2,400 ms"),
             Citation(evidence_id="nope", quote="anything at all")]
    assert groundedness(cites, EVIDENCE) == 0.5
    assert groundedness([], EVIDENCE) is None


# ---------- numbers ----------

@pytest.mark.parametrize("text", [
    "Latency rose to 2400 ms.",
    "Latency rose to 2,400 ms (2.4 s).",
    "About 12.5% of requests failed.",
    "17 of 40 requests failed between 05:10 and 05:20 on 2026-10-15.",
    "A rise of x13.3 on metric:latency and 1 window.",
    "The p95 on /v1/items/42 moved.",
])
def test_numbers_from_the_evidence_pass(text) -> None:
    assert check_numbers(text, "summary", EVIDENCE) == []


@pytest.mark.parametrize("text", ["Latency rose to 2500 ms.", "18 requests failed.", "It started at 05:11.",
                                  "Seen on 2026-10-16."])
def test_changed_numbers_are_blocked(text) -> None:
    assert codes(check_numbers(text, "summary", EVIDENCE)) == ["NUMBER_NOT_IN_EVIDENCE"]


def test_number_must_be_in_the_cited_items() -> None:
    text = "17 requests failed."
    assert check_numbers(text, "x", EVIDENCE, cited_ids=["log:window"]) == []
    assert codes(check_numbers(text, "x", EVIDENCE, cited_ids=["metric:latency"])) == ["NUMBER_NOT_IN_EVIDENCE"]


def test_also_allowed_numbers_are_names_not_measurements() -> None:
    assert codes(check_numbers("We saw 503 errors.", "x", EVIDENCE)) == ["NUMBER_NOT_IN_EVIDENCE"]
    assert check_numbers("We saw 503 errors.", "x", EVIDENCE, also_allowed=[503]) == []


# ---------- certainty ----------

@pytest.mark.parametrize("text", ["The root cause is the database.", "This is confirmed.",
                                  "It is definitely the cache.", "We are 100% sure."])
def test_certainty_is_blocked(text) -> None:
    assert codes(check_certainty(text, "summary")) == ["CERTAINTY"]


@pytest.mark.parametrize("text", ["This is not confirmed.", "It cannot be confirmed yet.",
                                  "The root cause is not known.", "Likely the database."])
def test_negated_or_hedged_text_passes(text) -> None:
    assert check_certainty(text, "summary") == []


# ---------- safe steps ----------

@pytest.mark.parametrize("action", ["Restart the service", "Delete the old rows", "Roll back the deploy",
                                    "rotating the key", "Drop the table", "Purge the cache"])
def test_changing_steps_are_blocked(action) -> None:
    assert codes(check_safe_steps(action, "steps[0].action")) == ["UNSAFE_STEP"]


@pytest.mark.parametrize("action", ["Compare p95 latency with the hour before", "Read the deploy log",
                                    "Query the error count by status"])
def test_read_only_steps_pass(action) -> None:
    assert check_safe_steps(action, "steps[0].action") == []


# ---------- secrets ----------

class Report(BaseModel):
    summary: str
    steps: list[str]


def test_secrets_anywhere_are_blocked_without_printing_them() -> None:
    email = "jane" + "@" + "example.com"
    key = "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"
    report = Report(summary=f"Ask {email}", steps=["Look at the log", f"Use key {key}"])
    findings = check_secrets(report)
    assert [(f.where, f.code) for f in findings] == [("summary", "SECRET"), ("steps[1]", "SECRET")]
    assert all(email not in f.message and key not in f.message for f in findings)


def test_masked_values_are_not_secrets() -> None:
    assert check_secrets({"a": "Bearer <TOKEN>", "b": ["gsk_<KEY>", "<EMAIL>", "203.0.113.x"]}) == []
