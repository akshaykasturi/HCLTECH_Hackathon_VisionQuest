"""Step 4: the code gate. Decides what happens to a ticket BEFORE any LLM runs (FR-05).

  RECOMMEND  strong, agreeing evidence: the LLM may write cited steps
  CLARIFY    one concrete question back to the user (too vague, or two likely issues)
  ESCALATE   a human group takes it, with the reason (no evidence, conflicting fixes, low confidence, safety)
  BLOCKED    an attack on the assistant (from screen.py): no search, no LLM

Rules are checked in a fixed order; the first one that fires wins. A pure function: the same inputs always
give the same decision, and the only file it reads is policy.yaml (the `gate` section). Thresholds are
placeholders until tuned on the val split.

"Strong" match = a similar ticket that is not a lookalike and scores at least similar.related_threshold.
Lookalikes never count as evidence for a recommendation.
"""
from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from typing import Any, Optional

import yaml

from app.tickets.models import Classification, GateDecision, KBHit, ScreenResult, SimilarTicket, TicketIn
from app.tickets.similar import POLICY_FILE
from app.tickets.similar import policy as similar_policy

# ---- Reason codes (what the analyst, the audit log and the judges see) -------------------------
SAFETY = "SAFETY"                        # hazard words (smoke, fire, spill): a person handles it now
TOO_VAGUE = "TOO_VAGUE"                  # too few words to search or classify reliably
NO_EVIDENCE = "NO_EVIDENCE"              # no classifier result and no strong similar ticket
NOTHING_SIMILAR = "NOTHING_SIMILAR"      # nothing in the history is close: a new kind of issue
TWO_LIKELY_ISSUES = "TWO_LIKELY_ISSUES"  # the classifier's top two categories are almost tied
SOURCES_DISAGREE = "SOURCES_DISAGREE"    # the classifier and the strong matches point to different categories
FIXES_CONFLICT = "FIXES_CONFLICT"        # the two best matches are equally close but had different root causes
DUPLICATE_FOUND = "DUPLICATE_FOUND"      # the best strong match is a likely duplicate
STRONG_MATCH = "STRONG_MATCH"            # a strong, agreeing match and a confident classifier
LOW_CONFIDENCE = "LOW_CONFIDENCE"        # some evidence, but not enough to recommend
# BLOCKED decisions carry screen.py's own codes (e.g. INJECTION_PATTERN).

DEFAULTS: dict[str, Any] = {
    "min_words": 5, "ood_threshold": 0.55, "ambiguity_margin": 0.10, "conflict_margin": 0.05,
    "min_class_prob": 0.60, "safety_group": "Desktop Support L2 (safety)", "default_group": "Service Desk L2",
    "escalation_groups": {}, "vague_question": "Please describe what you were doing, the exact error message, "
    "and which application or device is affected.", "clarify_questions": {},
}
GENERIC_QUESTION = ("Is this mainly about {a} or {b}? Please add the exact error message and the affected "
                    "application or device.")
_WORD = re.compile(r"\w+")


@lru_cache(maxsize=1)
def gate_policy() -> dict[str, Any]:
    """The `gate` section of policy.yaml with defaults, plus related_threshold from the `similar` section."""
    try:
        with POLICY_FILE.open(encoding="utf-8") as f:
            section = (yaml.safe_load(f) or {}).get("gate") or {}
    except FileNotFoundError:
        section = {}
    return {**DEFAULTS, **section, "related_threshold": similar_policy()["related_threshold"]}


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").lower().split())


def _question(a: str, b: str, p: dict[str, Any]) -> str:
    first, second = sorted([a, b])
    return p["clarify_questions"].get(f"{first}|{second}") or GENERIC_QUESTION.format(a=first, b=second)


def _majority_category(strong: list[SimilarTicket]) -> str:
    """Most common category among the strong matches; a tie goes to the best-scoring one's category."""
    counts = Counter(_norm(s.category) for s in strong)
    top = max(counts.values())
    return next(s.category for s in strong if counts[_norm(s.category)] == top)  # strong is best-first


def decide(ticket: TicketIn, screen: ScreenResult, classification: Optional[Classification],
           similar: list[SimilarTicket], kb: list[KBHit], policy: Optional[dict[str, Any]] = None) -> GateDecision:
    """RECOMMEND / CLARIFY / ESCALATE / BLOCKED with reason codes. `policy` overrides policy.yaml keys (tests).
    `kb` is accepted for the fixed signature; KB hits support the steps later but do not change the outcome."""
    p = {**gate_policy(), **(policy or {})}

    strong = sorted((s for s in similar if not s.lookalike and s.score >= p["related_threshold"]),
                    key=lambda s: -s.score)
    best_score = max((s.score for s in similar), default=None)
    parts = []
    if classification is not None:
        parts.append(classification.category.prob)
    if strong:
        parts.append(strong[0].score)
    elif best_score is not None:
        parts.append(best_score)
    confidence = round(min(parts), 4) if parts else 0.0

    def escalate(reason: str) -> GateDecision:
        label = classification.category.label if classification is not None else None
        group = p["escalation_groups"].get(label) if label else None
        return GateDecision(outcome="ESCALATE", reasons=[reason], confidence=confidence,
                            escalation_group=group or p["default_group"])

    def clarify(reason: str, question: str) -> GateDecision:
        return GateDecision(outcome="CLARIFY", reasons=[reason], confidence=confidence, question=question)

    # a. An attack on the assistant: stop here.
    if screen.blocked:
        return GateDecision(outcome="BLOCKED", reasons=list(screen.codes), confidence=0.0)
    # b. A safety hazard: a person, now.
    if screen.safety:
        return GateDecision(outcome="ESCALATE", reasons=[SAFETY], confidence=confidence,
                            escalation_group=p["safety_group"])
    # c. Too short to work with.
    if len(_WORD.findall(f"{ticket.subject} {ticket.description}")) < p["min_words"]:
        return clarify(TOO_VAGUE, p["vague_question"])
    # d. Neither a classifier result nor a strong match.
    if classification is None and not strong:
        return escalate(NO_EVIDENCE)
    # e. Nothing in the history is close.
    if best_score is None or best_score < p["ood_threshold"]:
        return escalate(NOTHING_SIMILAR)
    if classification is not None:
        # f. The classifier can't decide between two categories.
        top2 = sorted(classification.top3_category, key=lambda c: -c.prob)[:2]
        if len(top2) == 2 and top2[0].prob - top2[1].prob <= p["ambiguity_margin"]:
            return clarify(TWO_LIKELY_ISSUES, _question(top2[0].label, top2[1].label, p))
        # g. The classifier and the past tickets disagree.
        if strong:
            majority = _majority_category(strong)
            if _norm(majority) != _norm(classification.category.label):
                return clarify(SOURCES_DISAGREE, _question(classification.category.label, majority, p))
    # h. The two best matches were fixed for different reasons and are equally close.
    if (len(strong) >= 2 and strong[0].root_cause and strong[1].root_cause
            and _norm(strong[0].root_cause) != _norm(strong[1].root_cause)
            and strong[0].score - strong[1].score <= p["conflict_margin"]):
        return escalate(FIXES_CONFLICT)
    # i. Confident classifier and a strong match: the LLM may write cited steps.
    if classification is not None and classification.category.prob >= p["min_class_prob"] and strong:
        reason = DUPLICATE_FOUND if strong[0].match == "likely_duplicate" else STRONG_MATCH
        return GateDecision(outcome="RECOMMEND", reasons=[reason], confidence=confidence)
    # j. Everything else.
    return escalate(LOW_CONFIDENCE)
