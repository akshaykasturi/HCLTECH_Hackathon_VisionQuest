"""Shared data shapes for the IT ticket assistant (UC1).

Everyone codes against these. Change them only through a pull request that Akshay merges,
and tell the group: a renamed field breaks other people's code.
Field names follow the ticket data (data/tickets/tickets.csv); don't rename them.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.common.report_checks import Finding
from app.common.trace import TraceStep

Outcome = Literal["RECOMMEND", "CLARIFY", "ESCALATE", "BLOCKED"]
MatchLabel = Literal["likely_duplicate", "related", "weak"]
Verdict = Literal["useful", "incorrect", "unresolved"]
Role = Literal["analyst", "lead"]


# ---- Input -------------------------------------------------------------------------------

class TicketIn(BaseModel):
    """A new ticket as the analyst submits it. After step 2 the pipeline only keeps a masked copy."""
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    affected_service: Optional[str] = Field(default=None, max_length=100)
    environment: Optional[str] = Field(default=None, max_length=50)   # e.g. prod, staging, laptop
    channel: Optional[str] = Field(default=None, max_length=50)       # e.g. portal, email, phone
    created_at: Optional[datetime] = None                             # set by the API if missing


class TicketFilters(BaseModel):
    """FR-07 search filters (all optional)."""
    q: Optional[str] = Field(default=None, max_length=300)
    category: Optional[str] = None
    subcategory: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assigned_team: Optional[str] = None
    affected_service: Optional[str] = None
    environment: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None


# ---- Step 1: screen + classify (raw text, in memory only) -------------------------------

class ScreenResult(BaseModel):
    blocked: bool = False            # attack on the assistant -> outcome BLOCKED
    safety: bool = False             # hazard words -> ESCALATE as P1
    codes: list[str] = Field(default_factory=list)   # e.g. ["INJECTION_PATTERN"], ["SAFETY_SMOKE"]


class LabelScore(BaseModel):
    label: str
    prob: float = Field(ge=0.0, le=1.0)


class Classification(BaseModel):
    category: LabelScore
    subcategory: LabelScore
    priority: LabelScore
    top3_category: list[LabelScore] = Field(default_factory=list)
    key_terms: list[str] = Field(default_factory=list)       # words that pushed the prediction
    priority_rule: Optional[str] = None                      # e.g. "OUTAGE_BUMP" when a rule changed it
    model_version: str = "unknown"


# ---- Step 3: evidence ------------------------------------------------------------------------

class SimilarTicket(BaseModel):
    ticket_id: str
    subject: str
    category: str
    subcategory: Optional[str] = None
    status: str
    score: float                                              # fused score, higher = more similar
    match: MatchLabel
    why: list[str] = Field(default_factory=list)              # e.g. ["same affected_service", "same error code 809"]
    lookalike: bool = False                                   # shares keywords, different issue
    problem_id: Optional[str] = None                          # set when linked to an outage / open problem
    root_cause: Optional[str] = None                          # masked; the gate compares the top matches' causes
    resolution_text: Optional[str] = None                     # full resolution (masked); the LLM may cite it
    past_feedback: dict[str, int] = Field(default_factory=dict)   # {"useful": 3, "incorrect": 1}


class KBHit(BaseModel):
    section_id: str                                           # doc_search id, e.g. "doc:kb/vpn-809#2"
    title: str
    text: str                                                 # full section text; the LLM may cite it
    score: float


# ---- Step 4: gate ----------------------------------------------------------------------------

class GateDecision(BaseModel):
    outcome: Outcome
    reasons: list[str] = Field(default_factory=list)          # reason codes, e.g. ["STRONG_MATCH"]
    confidence: float = Field(ge=0.0, le=1.0)
    question: Optional[str] = None                            # set when outcome is CLARIFY
    escalation_group: Optional[str] = None                    # set when outcome is ESCALATE


# ---- Steps 5-6: LLM output and checks --------------------------------------------------------

class Step(BaseModel):
    text: str                                                 # the instruction shown to the analyst
    source_id: str                                            # a ticket_id or KB section_id from the evidence
    excerpt: str                                              # words copied exactly from that source
    why: str                                                  # why this source supports the step


class Recommendation(BaseModel):
    summary: str
    steps: list[Step] = Field(default_factory=list)
    escalation_group: Optional[str] = None
    generator: str = "template"                               # "gemini" | "groq" | "template"
    prompt_version: str = "v1"


# ---- The whole result ------------------------------------------------------------------------

class AnalysisResult(BaseModel):
    result_id: str
    created_at: datetime                                      # ticket received
    recommended_at: Optional[datetime] = None                 # pipeline finished (for MTTR)
    ticket: TicketIn                                          # MASKED copy only
    screen: ScreenResult = Field(default_factory=ScreenResult)
    classification: Optional[Classification] = None
    similar: list[SimilarTicket] = Field(default_factory=list)
    kb: list[KBHit] = Field(default_factory=list)
    outcome: Outcome
    reasons: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    question: Optional[str] = None
    recommendation: Optional[Recommendation] = None
    findings: list[Finding] = Field(default_factory=list)
    timings_ms: dict[str, float] = Field(default_factory=dict)
    trace: list[TraceStep] = Field(default_factory=list)


# ---- Review and auth -------------------------------------------------------------------------

class FeedbackIn(BaseModel):
    verdict: Verdict
    note: Optional[str] = Field(default=None, max_length=2000)     # masked before storage
    target: Literal["result", "match"] = "result"
    target_id: Optional[str] = None                               # ticket_id when target == "match"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class User(BaseModel):
    username: str
    role: Role
