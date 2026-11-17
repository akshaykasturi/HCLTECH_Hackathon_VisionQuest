"""The five tables (docs/ARCHITECTURE.md, "Database") and init_db().

  users             demo users from DEMO_USERS, PBKDF2 hashes only
  analysis_results  one row per analyze() run; MASKED data only (the pipeline masks in step 2)
  feedback          append-only: a changed mind is a new row; the latest row counts
  resolutions       when a result was resolved (MTTR end time); one row per result
  audit_events      hash chain over every write to the tables above

user_id, reviewer_id and resolved_by hold the USERNAME (the identity in the login token), not a foreign key to
users.id: login can fall back to DEMO_USERS when a user is not in the table, and their work must still be saved.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.engine import get_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AnalysisResultRow(Base):
    __tablename__ = "analysis_results"
    result_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    recommended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="analyst")   # analyst | employee
    ticket_masked: Mapped[dict] = mapped_column(JSON, nullable=False)
    classification: Mapped[dict | None] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommendation: Mapped[dict | None] = mapped_column(JSON)
    findings: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    generator: Mapped[str | None] = mapped_column(String(30))
    model_version: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(30))


class FeedbackRow(Base):
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    result_id: Mapped[str] = mapped_column(ForeignKey("analysis_results.result_id"), nullable=False, index=True)
    reviewer_id: Mapped[str] = mapped_column(String(50), nullable=False)
    target: Mapped[str] = mapped_column(String(10), nullable=False, default="result")     # result | match
    target_id: Mapped[str | None] = mapped_column(String(100), index=True)                # ticket_id for a match
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)                      # useful | incorrect | unresolved
    note_masked: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ResolutionRow(Base):
    __tablename__ = "resolutions"
    result_id: Mapped[str] = mapped_column(ForeignKey("analysis_results.result_id"), primary_key=True)
    resolved_by: Mapped[str] = mapped_column(String(50), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    result_id: Mapped[str | None] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)        # what was written (masked, no hashes)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def init_db() -> list[str]:
    """Create missing tables, then add the DEMO_USERS that are not in the users table yet (an existing user is
    never overwritten). Returns the usernames added. Safe to run at every startup (main.py warm-up)."""
    from app.auth.users import parse_demo_users   # here, not at the top: auth imports db/repo lazily too
    from app.db import repo

    Base.metadata.create_all(get_engine())
    return repo.add_missing_users(parse_demo_users(os.getenv("DEMO_USERS", "")))
