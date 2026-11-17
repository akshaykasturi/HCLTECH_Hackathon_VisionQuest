"""Step 5: the LLM words cited fix steps (FR-03). It runs only when the gate said RECOMMEND.

    rec = generate(masked_ticket, classification, similar, kb, allowed_ids)   # Recommendation | None

What comes back is NOT shown yet: the pipeline sends it through tickets/checks.py first.

Three possible results:
- a Recommendation with steps          the LLM answered; checks.py verifies each step next
- a Recommendation with NO steps       the LLM said no evidence fits: the checks find nothing to keep and
                                       the pipeline escalates to a person (better than a weak guess)
- None                                 no LLM answered (no key, timeout, bad JSON twice) or there is nothing
                                       to cite: the pipeline uses its template, which copies a past ticket's
                                       steps word for word

The LLM goes through common/llm.generate_json (Gemini, then Groq). Keys never leave that module.
For evaluation and the red-team runner, generate_detailed() also returns the provider and the (masked) errors.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from pydantic import BaseModel, Field

from app.common.llm import HttpPost, Provider, generate_json
from app.tickets.models import Classification, KBHit, Recommendation, SimilarTicket, Step, TicketIn
from app.tickets.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_message, shown_evidence_ids

logger = logging.getLogger(__name__)

MAX_STEPS = 5


# What the LLM must return. Kept separate from Recommendation: the LLM never sets generator or prompt_version.
class LLMStep(BaseModel):
    text: str = Field(min_length=1, max_length=400)
    source_id: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=600)
    why: str = Field(min_length=1, max_length=300)


class LLMRecommendation(BaseModel):
    summary: str = Field(min_length=1, max_length=600)
    steps: list[LLMStep] = Field(default_factory=list)


class GenerateResult(NamedTuple):
    recommendation: Optional[Recommendation]
    provider: Optional[str]
    errors: list[str]           # masked, safe to show in a trace or report


def generate_detailed(ticket: TicketIn, classification: Optional[Classification], similar: list[SimilarTicket],
                      kb: list[KBHit], allowed_ids: list[str], *, http: Optional[HttpPost] = None,
                      providers: Optional[list[Provider]] = None) -> GenerateResult:
    """generate(), plus which provider answered and what went wrong. Tests pass a fake `http`."""
    if not shown_evidence_ids(similar, kb, allowed_ids):
        return GenerateResult(None, None, ["no allowed evidence to cite"])
    user = build_user_message(ticket, classification, similar, kb, allowed_ids)

    data, provider, errors = generate_json(SYSTEM_PROMPT, user, LLMRecommendation, http=http, providers=providers)
    if data is None:
        return GenerateResult(None, None, errors)

    steps = [Step(text=s.text.strip(), source_id=s.source_id.strip(), excerpt=s.excerpt.strip(), why=s.why.strip())
             for s in data.steps[:MAX_STEPS]]
    if len(data.steps) > MAX_STEPS:
        errors.append(f"{provider}: {len(data.steps)} steps returned, kept the first {MAX_STEPS}")
    rec = Recommendation(summary=data.summary.strip(), steps=steps, generator=provider or "unknown",
                         prompt_version=PROMPT_VERSION)
    return GenerateResult(rec, provider, errors)


def generate(ticket: TicketIn, classification: Optional[Classification], similar: list[SimilarTicket],
             kb: list[KBHit], allowed_ids: list[str]) -> Optional[Recommendation]:
    """The pipeline's plug (signature fixed in docs/PLAN.md)."""
    result = generate_detailed(ticket, classification, similar, kb, allowed_ids)
    if result.recommendation is None and result.errors:
        logger.info("generate: no LLM recommendation (%d problems; template used)", len(result.errors))
    return result.recommendation
