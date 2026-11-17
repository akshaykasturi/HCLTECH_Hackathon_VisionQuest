"""The whole analysis for one ticket: analyze(ticket, user) -> AnalysisResult (POST /api/tickets/analyze).

  1 screen + classify   RAW text, in memory only (mentor's order). Blocked -> nothing else runs.
  2 mask                mask_ticket; from here on only the masked copy is used. If masking fails we stop.
  3 find evidence       similar past tickets (similar.py) + KB sections (kb.py)
  4 gate                decide(): RECOMMEND / CLARIFY / ESCALATE / BLOCKED, in code
  5 write steps         RECOMMEND only: the LLM (generate.py) words cited steps, checks.py verifies them;
                        without a working LLM a template copies the steps of the best past ticket verbatim
  6 result              masked ticket, evidence, decision, steps, timings and trace
  7 save                db/repo.save_result; a database error never fails the request

Teammates' parts are loaded with trace.optional(): a missing or failing part gets a simple fallback and a
trace note, so the pipeline always answers. Two things never fall back: masking (no raw text may continue)
and checks (every recommendation, LLM or template, goes through them; unchecked LLM output is never shown).

Trace notes contain step names, counts and error class names only, never ticket text.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.common.redact import mask
from app.common.trace import Trace, optional
from app.tickets.gate import decide, escalation_group_for, gate_policy
from app.tickets.guard import mask_ticket
from app.tickets.models import (AnalysisResult, Classification, GateDecision, KBHit, Recommendation, ScreenResult,
                                SimilarTicket, Step, TicketIn, User)
from app.tickets.screen import screen
from app.tickets.similar import get_ticket_index, search_similar

logger = logging.getLogger(__name__)

SCREEN_ERROR = "SCREEN_ERROR"              # the screen step failed: fail closed, a person takes it
NO_VERIFIED_STEPS = "NO_VERIFIED_STEPS"    # RECOMMEND, but no step survived the checks: a person takes it


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _err(exc: BaseException) -> str:
    return exc.__class__.__name__  # never the message: it could quote ticket text


def data_escalation_groups() -> dict[str, str]:
    """{category: majority assigned_team} learned from the ticket index ({} if it cannot be built)."""
    try:
        return dict(get_ticket_index().teams_by_category)
    except Exception as exc:
        logger.warning("escalation teams from data unavailable (%s)", _err(exc))
        return {}


def _escalation_group(classification: Optional[Classification], groups: dict[str, str]) -> str:
    label = classification.category.label if classification is not None else None
    return escalation_group_for(label, {**gate_policy(), "data_escalation_groups": groups})


def _steps_of(ticket: SimilarTicket) -> list[str]:
    text = ticket.resolution_text or ""
    if "Steps:" not in text:
        return []
    return [s.strip() for s in text.rsplit("Steps:", 1)[1].split(" | ") if s.strip()]


def template_recommendation(similar: list[SimilarTicket]) -> Optional[Recommendation]:
    """Steps copied word for word from the best strong past ticket (not a lookalike, score >= related_threshold)
    that HAS resolution steps: open or in-progress tickets have none, so they are skipped, not a dead end.
    No LLM: each step's excerpt IS its text, so it is verbatim by construction. None if there is nothing to copy.
    `why` names the ticket but not the similarity score: the number check only allows numbers from the source."""
    threshold = gate_policy()["related_threshold"]
    strong = sorted((s for s in similar if not s.lookalike and s.score >= threshold), key=lambda s: -s.score)
    best = next((s for s in strong if _steps_of(s)), None)
    if best is None:
        return None
    texts = _steps_of(best)
    why = f"Resolved ticket {best.ticket_id} (match: {best.match.replace('_', ' ')})"
    steps = [Step(text=s, source_id=best.ticket_id, excerpt=s, why=why) for s in texts]
    if not steps:
        return None
    return Recommendation(summary=f"Steps that resolved the similar ticket {best.ticket_id} ({best.subject}).",
                          steps=steps, generator="template")


def _write_steps(masked: TicketIn, classification: Optional[Classification], similar: list[SimilarTicket],
                 kb: list[KBHit], trace: Trace) -> tuple[Optional[Recommendation], list[Any]]:
    """Step 5: LLM steps that passed the checks, or the template. Returns (recommendation, findings)."""
    allowed_ids = [s.ticket_id for s in similar if not s.lookalike] + [k.section_id for k in kb]
    rec: Optional[Recommendation] = None
    with trace.step("generate") as note:
        generate = optional("app.tickets.generate", "generate")
        if generate is None:
            note["detail"] = "generate.py not ready: template used"
        else:
            try:
                rec = generate(masked, classification, similar, kb, allowed_ids)
                note["detail"] = f"generator {rec.generator}" if rec else "LLM gave nothing: template used"
            except Exception as exc:
                note["detail"] = f"generate failed ({_err(exc)}): template used"
                rec = None
    if rec is None:
        rec = template_recommendation(similar)
        if rec is None:
            return None, []

    # Every recommendation goes through the checks, the template too: a past ticket's own fix can be
    # unsafe ("restart the server") or carry a secret. Found by Atheesha.
    with trace.step("checks") as note:
        checks = optional("app.tickets.checks", "check_recommendation")
        if checks is None:
            # Unchecked LLM output is never shown. The template is shown unchecked: its steps are quotes.
            note["detail"] = "checks not merged: template shown, not checked"
            return (rec if rec.generator == "template" else template_recommendation(similar)), []
        try:
            checked, findings = checks(rec, similar, kb, allowed_ids)
        except Exception as exc:  # the checks broke on this input: show nothing unchecked, escalate
            note["detail"] = f"checks failed ({_err(exc)}): nothing shown"
            return None, []
        kept = len(checked.steps) if checked else 0
        note["detail"] = f"{rec.generator}: {kept} of {len(rec.steps)} steps passed, {len(findings)} findings"
        return checked, list(findings)


def analyze(ticket: TicketIn, user: User, exclude_ticket_ids: Iterable[str] = ()) -> AnalysisResult:
    """Run steps 1-7 for one ticket. Never returns raw text; raises only if masking fails.
    exclude_ticket_ids: past tickets similar search must not return. Presets and evaluations pass the source
    ticket's own id (leave-one-out): it is in the index, and finding itself would fake a perfect match."""
    trace = Trace()
    result_id = uuid.uuid4().hex
    created_at = ticket.created_at or _now()
    classification: Optional[Classification] = None
    similar: list[SimilarTicket] = []
    kb: list[KBHit] = []
    recommendation: Optional[Recommendation] = None
    findings: list[Any] = []

    # ---- Step 1: screen + classify, on the raw text, in memory only --------------------------
    screen_result: Optional[ScreenResult] = None
    with trace.step("screen") as note:
        try:
            screen_result = screen(ticket)
            note["detail"] = f"codes: {', '.join(screen_result.codes)}" if screen_result.codes else "clean"
        except Exception as exc:
            note["detail"] = f"screen failed ({_err(exc)}): failing closed"

    if screen_result is not None and not screen_result.blocked:
        with trace.step("classify") as note:
            classify = optional("app.tickets.classify", "classify")
            if classify is None:
                note["detail"] = "classify.py not ready: no classification"
            else:
                try:
                    classification = classify(ticket)
                    # key terms come from the raw text: mask them before they are kept anywhere
                    classification = classification.model_copy(
                        update={"key_terms": [mask(t) for t in classification.key_terms]})
                    note["detail"] = (f"{classification.category.label} ({classification.category.prob:.2f}), "
                                      f"model {classification.model_version}")
                except Exception as exc:
                    classification = None
                    note["detail"] = f"classify failed ({_err(exc)}): no classification"

    # ---- Step 2: mask. Any error here stops the request: raw text must not go further --------
    with trace.step("mask") as note:
        masked, counts = mask_ticket(ticket)
        note["detail"] = ("masked " + ", ".join(f"{n} {kind}" for kind, n in sorted(counts.items()))
                          if counts else "nothing to mask")
    del ticket  # from here on only `masked` exists

    # ---- Steps 3-5 -------------------------------------------------------------------------------
    if screen_result is None:
        decision = GateDecision(outcome="ESCALATE", reasons=[SCREEN_ERROR], confidence=0.0,
                                escalation_group=_escalation_group(None, {}))
    elif screen_result.blocked:
        with trace.step("gate") as note:
            decision = decide(masked, screen_result, None, [], [])
            note["detail"] = "BLOCKED: no search, no LLM"
    else:
        query = f"{masked.subject}. {masked.description}"
        with trace.step("similar") as note:
            similar = search_similar(query, category_hint=classification.category.label if classification else None,
                                     affected_service=masked.affected_service, exclude_ticket_ids=exclude_ticket_ids)
            note["detail"] = (f"{len(similar)} found, best {similar[0].score:.2f} ({similar[0].match})"
                              if similar else "nothing found")
        with trace.step("kb") as note:
            search_kb = optional("app.tickets.kb", "search_kb")
            if search_kb is None:
                note["detail"] = "kb.py not ready: no KB sections"
            else:
                try:
                    kb = search_kb(query) or []
                    note["detail"] = f"{len(kb)} sections"
                except Exception as exc:
                    note["detail"] = f"kb search failed ({_err(exc)}): no KB sections"
        groups = data_escalation_groups()
        with trace.step("gate") as note:
            decision = decide(masked, screen_result, classification, similar, kb,
                              policy={"data_escalation_groups": groups})
            note["detail"] = f"{decision.outcome}: {', '.join(decision.reasons)}"

        if decision.outcome == "RECOMMEND":
            recommendation, findings = _write_steps(masked, classification, similar, kb, trace)
            if recommendation is None or not recommendation.steps:
                decision = GateDecision(outcome="ESCALATE", reasons=decision.reasons + [NO_VERIFIED_STEPS],
                                        confidence=decision.confidence,
                                        escalation_group=_escalation_group(classification, groups))
                recommendation = None

    # ---- Step 6: the result (masked data only) ---------------------------------------------------
    result = AnalysisResult(
        result_id=result_id, created_at=created_at, recommended_at=_now(), ticket=masked,
        screen=screen_result or ScreenResult(), classification=classification, similar=similar, kb=kb,
        outcome=decision.outcome, reasons=decision.reasons, confidence=decision.confidence,
        question=decision.question, escalation_group=decision.escalation_group,
        recommendation=recommendation, findings=findings)

    # ---- Step 7: save; a database problem never fails the request ---------------------------------
    with trace.step("save") as note:
        save = optional("app.db.repo", "save_result")
        if save is None:
            note["detail"] = "db/repo.py not ready: not saved"
        else:
            try:
                result.trace = list(trace.steps)
                result.timings_ms = {s.step: s.ms for s in trace.steps}
                save(result, user)
                note["detail"] = "saved"
            except Exception as exc:
                logger.error("saving result %s failed (%s)", result_id, _err(exc))
                note["detail"] = f"save failed ({_err(exc)}): result not stored"

    result.trace = list(trace.steps)
    result.timings_ms = {s.step: s.ms for s in trace.steps}
    return result
