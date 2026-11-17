"""Step 6: code checks the LLM's recommendation before any analyst sees it (FR-03, FR-04, FR-05).

    checked, findings = check_recommendation(rec, similar, kb, allowed_ids)
    if checked is None:   # no step survived: the pipeline escalates instead of guessing
        ...

Each step is checked on its own. A step that fails any "block" check is DROPPED (never shown):

  STEP_SOURCE_NOT_ALLOWED   source_id is not one of the allowed evidence ids
  STEP_SOURCE_LOOKALIKE     the cited ticket only shares keywords; it is a different issue
  STEP_SOURCE_NOT_RESOLVED  the cited ticket was never fixed, so its notes are not a verified fix
  CITE_*                    the excerpt is not copied word for word from the cited source (or too short)
  NUMBER_NOT_IN_EVIDENCE    a number in the step is not in the cited source
  CERTAINTY                 "confirmed", "definitely"... in the LLM's own words
  UNSAFE_STEP               a changing action (restart the server, delete, roll back): a human decides
  SECRET                    a token, key, email, phone or password in the step
  STEP_DUPLICATE            the same step twice

Warnings keep the step but tell the reviewer: STEP_TEXT_UNRELATED (the step shares no word with its excerpt).

The summary gets the same certainty, number and secret checks. If it fails, a fixed summary written by code
replaces it (the steps are still shown). No steps left -> (None, findings).

Two rules that matter:
- The certainty check runs on what the LLM WROTE (text, why, summary), not on the excerpt. An excerpt is a
  quote: if a past ticket says "Confirmed the router allows IPsec", quoting it is honest.
- Everyday user-device fixes ("restart the laptop", "disable power saving") are allowed; the list is in
  checks.yaml. Server, account and data changes stay blocked.

Pure: reads, returns, changes nothing. Settings in checks.yaml; shared word lists in common/report_checks.yaml.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from app.common.report_checks import (Citation, Evidence, Finding, check_certainty, check_numbers,
                                      check_safe_steps, check_secrets, citation_problem)
from app.tickets.models import KBHit, Recommendation, SimilarTicket, Step

_SETTINGS_FILE = Path(__file__).with_name("checks.yaml")
_WORD_RE = re.compile(r"[a-z]+")


@lru_cache(maxsize=1)
def _settings() -> dict[str, Any]:
    with _SETTINGS_FILE.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    raw["_safe_res"] = [re.compile(p) for p in raw["safe_user_actions"]]
    raw["_resolved"] = {s.lower() for s in raw["resolved_statuses"]}
    return raw


def _block(code: str, message: str, where: str) -> Finding:
    return Finding(code=code, severity="block", message=message, where=where)


def build_evidence(similar: list[SimilarTicket], kb: list[KBHit]) -> list[Evidence]:
    """What a step may quote. A past ticket: its subject and its (masked) resolution. A KB hit: its text."""
    items = [Evidence(id=t.ticket_id, text=f"{t.subject}\n{t.resolution_text or ''}".strip()) for t in similar]
    items += [Evidence(id=k.section_id, text=f"{k.title}\n{k.text}") for k in kb]
    return items


def _without_safe_user_actions(text: str) -> str:
    low = " ".join(text.lower().split())
    for pattern in _settings()["_safe_res"]:
        low = pattern.sub(" ", low)
    return low


def _words(text: str) -> set[str]:
    n = _settings()["min_shared_word_len"]
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) >= n}


def check_step(step: Step, where: str, evidence: list[Evidence], allowed: set[str],
               similar_by_id: dict[str, SimilarTicket]) -> list[Finding]:
    """All findings for one step. Any "block" finding means the step must not be shown."""
    by_id = {e.id: e for e in evidence}
    if step.source_id not in allowed or step.source_id not in by_id:
        return [_block("STEP_SOURCE_NOT_ALLOWED", f"{step.source_id!r} is not in the allowed evidence", where)]

    findings: list[Finding] = []
    ticket = similar_by_id.get(step.source_id)
    if ticket is not None:
        if ticket.lookalike:
            findings.append(_block("STEP_SOURCE_LOOKALIKE",
                                   f"{step.source_id} only shares keywords: it is a different issue", where))
        if ticket.status.lower() not in _settings()["_resolved"]:
            findings.append(_block("STEP_SOURCE_NOT_RESOLVED",
                                   f"{step.source_id} is {ticket.status!r}: not a verified fix", where))

    problem = citation_problem(Citation(evidence_id=step.source_id, quote=step.excerpt), by_id)
    if problem:
        findings.append(_block(*problem, f"{where}.excerpt"))

    # Numbers must come from the cited source only (not from some other ticket).
    for part in ("text", "why"):
        findings += check_numbers(getattr(step, part), f"{where}.{part}", evidence, cited_ids=[step.source_id])
        findings += check_certainty(getattr(step, part), f"{where}.{part}")
    findings += check_safe_steps(_without_safe_user_actions(step.text), f"{where}.text")
    findings += check_secrets(step, where)

    if not _words(step.text) & _words(step.excerpt):
        findings.append(Finding(code="STEP_TEXT_UNRELATED", severity="warn", where=f"{where}.text",
                                message="the step shares no word with its excerpt: check it says the same thing"))
    return findings


def check_summary(summary: str, evidence: list[Evidence]) -> list[Finding]:
    return (check_certainty(summary, "summary") + check_numbers(summary, "summary", evidence)
            + check_secrets(summary, "summary"))


def check_recommendation(rec: Recommendation, similar: list[SimilarTicket], kb: list[KBHit],
                         allowed_ids: list[str]) -> tuple[Optional[Recommendation], list[Finding]]:
    """The recommendation with failing steps dropped (and a safe summary if needed), plus every finding.
    Returns (None, findings) when no step survives."""
    evidence = build_evidence(similar, kb)
    allowed = set(allowed_ids)
    similar_by_id = {t.ticket_id: t for t in similar}

    findings: list[Finding] = []
    kept: list[Step] = []
    seen: set[str] = set()
    for i, step in enumerate(rec.steps):
        where = f"steps[{i}]"
        key = " ".join(step.text.lower().split())
        if key in seen:
            findings.append(_block("STEP_DUPLICATE", "the same step appears twice", where))
            continue
        step_findings = check_step(step, where, evidence, allowed, similar_by_id)
        findings += step_findings
        if not any(f.severity == "block" for f in step_findings):
            kept.append(step)
            seen.add(key)

    if not kept:
        findings.append(_block("NO_STEPS_LEFT", "no step passed the checks: escalate instead", "steps"))
        return None, findings

    summary = rec.summary
    summary_findings = check_summary(summary, evidence)
    if summary_findings:
        findings += summary_findings
        summary = _settings()["fallback_summary"]
    return rec.model_copy(update={"steps": kept, "summary": summary}), findings
