"""The repository: the ONLY module that reads or writes the database (Neon Postgres, or SQLite locally).

  users      get_user, add_missing_users (init_db seeds DEMO_USERS; an existing user is never overwritten)
  results    save_result (pipeline step 7), history (GET /history)
  review     add_feedback (append-only), resolve (idempotent: the first time wins)
  reading    stats (GET /stats, computed live), feedback_counts (SimilarTicket.past_feedback)
  audit      every write also appends an audit_events row; verify_chain() re-checks the whole chain

Only masked data is stored. AnalysisResult is masked by the pipeline (step 2); save_result re-checks the ticket's
text fields with common.redact.mask and refuses to store anything that still contains a secret. Feedback notes are
masked here.

Audit chain: each event's payload records what was written, including row_sha256, the SHA-256 of the row's
canonical JSON. hash = common.audit.compute_hash(seq, type, time, result_id, canonical_json(payload), prev_hash).
verify_chain() recomputes every hash AND every row_sha256 from the rows as they are now, so editing a stored
result, feedback, resolution or user row, or an audit event, is detected (first_broken_seq). Like any hash chain
it detects tampering; someone who can rewrite every row and every hash can still forge it.

Errors: an unknown result_id raises LookupError (the API answers 404). No ticket text is ever logged.
"""
from __future__ import annotations

import hashlib
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.common.audit import GENESIS_HASH, canonical_json, compute_hash
from app.common.redact import mask
from app.db.engine import get_session
from app.db.tables import AnalysisResultRow, AuditEventRow, FeedbackRow, ResolutionRow, UserRow, utcnow
from app.tickets.models import AnalysisResult, FeedbackIn, User

VERDICTS = ("useful", "incorrect", "unresolved")
TEXT_FIELDS = ("subject", "description", "affected_service", "environment", "channel", "category_hint")
_PG_AUDIT_LOCK = 7_331_001   # advisory lock id: one audit writer at a time on Postgres


class NotMaskedError(ValueError):
    """A result still holds a secret or personal data in its ticket text: it is not stored."""


# ---- small helpers ---------------------------------------------------------------------------

def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite gives back naive datetimes; treat them as UTC so both databases answer the same."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    d = _utc(dt)
    return d.isoformat() if d else None


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _result_or_raise(session: Session, result_id: str) -> AnalysisResultRow:
    row = session.get(AnalysisResultRow, result_id)
    if row is None:
        raise LookupError("unknown result_id")
    return row


# Canonical content of each row (what row_sha256 covers). JSON-safe, UTC ISO timestamps.

def _user_content(r: UserRow) -> dict[str, Any]:
    return {"username": r.username, "role": r.role, "password_hash": r.password_hash, "created_at": _iso(r.created_at)}


def _result_content(r: AnalysisResultRow) -> dict[str, Any]:
    return {"result_id": r.result_id, "created_at": _iso(r.created_at), "recommended_at": _iso(r.recommended_at),
            "user_id": r.user_id, "source": r.source, "ticket_masked": r.ticket_masked,
            "classification": r.classification, "evidence_ids": r.evidence_ids, "outcome": r.outcome,
            "reasons": r.reasons, "confidence": r.confidence, "recommendation": r.recommendation,
            "findings": r.findings, "generator": r.generator, "model_version": r.model_version,
            "prompt_version": r.prompt_version}


def _feedback_content(r: FeedbackRow) -> dict[str, Any]:
    return {"id": r.id, "result_id": r.result_id, "reviewer_id": r.reviewer_id, "target": r.target,
            "target_id": r.target_id, "verdict": r.verdict, "note_masked": r.note_masked,
            "created_at": _iso(r.created_at)}


def _resolution_content(r: ResolutionRow) -> dict[str, Any]:
    return {"result_id": r.result_id, "resolved_by": r.resolved_by, "resolved_at": _iso(r.resolved_at)}


# ---- audit chain -----------------------------------------------------------------------------

def _event_hash(seq: int, event_type: str, ts: str, result_id: Optional[str], payload: dict, prev: str) -> str:
    return compute_hash(seq, str(seq), ts, event_type, "", result_id or "", None, canonical_json(payload), prev)


def _audit(session: Session, event_type: str, result_id: Optional[str], payload: dict[str, Any]) -> None:
    """Append one event in the caller's transaction (the row and its audit event commit together)."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_AUDIT_LOCK})
    session.flush()   # the written row gets its id before we hash it
    last = session.execute(select(AuditEventRow).order_by(AuditEventRow.seq.desc()).limit(1)).scalar_one_or_none()
    seq = (last.seq + 1) if last else 1
    prev = last.hash if last else GENESIS_HASH
    now = utcnow()
    session.add(AuditEventRow(seq=seq, event_type=event_type, result_id=result_id, payload=payload,
                              payload_hash=_sha(payload), prev_hash=prev, created_at=now,
                              hash=_event_hash(seq, event_type, _iso(now), result_id, payload, prev)))


def _current_row_sha(session: Session, ev: AuditEventRow) -> Optional[str]:
    """row_sha256 of the row this event wrote, as the row is NOW (None if the row is gone)."""
    p = ev.payload
    if ev.event_type == "result_saved":
        row = session.get(AnalysisResultRow, p.get("result_id"))
        return _sha(_result_content(row)) if row else None
    if ev.event_type == "feedback_added":
        row = session.get(FeedbackRow, p.get("feedback_id"))
        return _sha(_feedback_content(row)) if row else None
    if ev.event_type == "resolved":
        row = session.get(ResolutionRow, p.get("result_id"))
        return _sha(_resolution_content(row)) if row else None
    if ev.event_type == "user_added":
        row = session.execute(select(UserRow).where(UserRow.username == p.get("username"))).scalar_one_or_none()
        return _sha(_user_content(row)) if row else None
    return p.get("row_sha256")


def verify_chain() -> dict[str, Any]:
    """{ok, first_broken_seq, n_events}. Checks each event's links and hash, and that the row it wrote is unchanged."""
    with get_session() as s:
        prev = GENESIS_HASH
        n = 0
        for ev in s.execute(select(AuditEventRow).order_by(AuditEventRow.seq)).scalars():
            n += 1
            ok = (ev.prev_hash == prev and ev.payload_hash == _sha(ev.payload)
                  and ev.hash == _event_hash(ev.seq, ev.event_type, _iso(ev.created_at), ev.result_id, ev.payload,
                                             ev.prev_hash)
                  and _current_row_sha(s, ev) == ev.payload.get("row_sha256"))
            if not ok:
                return {"ok": False, "first_broken_seq": ev.seq, "n_events": n}
            prev = ev.hash
        return {"ok": True, "first_broken_seq": None, "n_events": n}


# ---- users -----------------------------------------------------------------------------------

def get_user(username: str) -> Optional[dict[str, Any]]:
    """{username, role, password_hash} or None. Used by app/auth/users.find_user for login."""
    with get_session() as s:
        row = s.execute(select(UserRow).where(UserRow.username == username)).scalar_one_or_none()
        return {"username": row.username, "role": row.role, "password_hash": row.password_hash} if row else None


def add_missing_users(users: Iterable[tuple[str, str, str]]) -> list[str]:
    """Insert (username, password, role) for usernames not in the table yet; never overwrites. Returns those added.
    The password is hashed here (PBKDF2) and the plain text is not kept."""
    added = []
    with get_session() as s:
        existing = set(s.execute(select(UserRow.username)).scalars())
        for username, password, role in users:
            if username in existing:
                continue
            row = UserRow(username=username, password_hash=hash_password(password), role=role, created_at=utcnow())
            s.add(row)
            s.flush()
            _audit(s, "user_added", None, {"username": username, "role": role, "row_sha256": _sha(_user_content(row))})
            existing.add(username)
            added.append(username)
    return added


# ---- results ---------------------------------------------------------------------------------

def _check_masked(result: AnalysisResult) -> None:
    for field in TEXT_FIELDS:
        value = getattr(result.ticket, field, None)
        if isinstance(value, str) and mask(value) != value:
            raise NotMaskedError(f"ticket.{field} is not masked")


def save_result(result: AnalysisResult, user: User) -> None:
    """Store one pipeline result (masked) + its audit event. Called by pipeline step 7."""
    _check_masked(result)
    rec = result.recommendation
    row = AnalysisResultRow(
        result_id=result.result_id, created_at=_utc(result.created_at), recommended_at=_utc(result.recommended_at),
        user_id=user.username, source="employee" if user.role == "employee" else "analyst",
        ticket_masked=result.ticket.model_dump(mode="json"),
        classification=result.classification.model_dump(mode="json") if result.classification else None,
        evidence_ids=[s.ticket_id for s in result.similar] + [k.section_id for k in result.kb],
        outcome=result.outcome, reasons=list(result.reasons), confidence=result.confidence,
        recommendation=rec.model_dump(mode="json") if rec else None,
        findings=[f.model_dump(mode="json") for f in result.findings],
        generator=rec.generator if rec else None,
        model_version=result.classification.model_version if result.classification else None,
        prompt_version=rec.prompt_version if rec else None)
    with get_session() as s:
        s.add(row)
        s.flush()
        row = s.get(AnalysisResultRow, result.result_id)   # read back so the hash covers what was stored
        _audit(s, "result_saved", row.result_id, {"result_id": row.result_id, "outcome": row.outcome,
                                                  "user_id": row.user_id, "row_sha256": _sha(_result_content(row))})


def add_feedback(result_id: str, user: User, feedback: FeedbackIn) -> dict[str, Any]:
    """Append one verdict (never overwrites older ones). LookupError if result_id is unknown."""
    with get_session() as s:
        _result_or_raise(s, result_id)
        row = FeedbackRow(result_id=result_id, reviewer_id=user.username, target=feedback.target,
                          target_id=feedback.target_id, verdict=feedback.verdict,
                          note_masked=mask(feedback.note) if feedback.note else None, created_at=utcnow())
        s.add(row)
        s.flush()
        _audit(s, "feedback_added", result_id, {"feedback_id": row.id, "result_id": result_id,
                                                "verdict": row.verdict, "row_sha256": _sha(_feedback_content(row))})
        return {"id": row.id, "result_id": result_id, "reviewer": row.reviewer_id, "target": row.target,
                "target_id": row.target_id, "verdict": row.verdict, "created_at": _iso(row.created_at)}


def resolve(result_id: str, user: User) -> datetime:
    """Store when the result was resolved (MTTR end). A second call returns the first time and writes nothing."""
    with get_session() as s:
        _result_or_raise(s, result_id)
        existing = s.get(ResolutionRow, result_id)
        if existing is not None:
            return _utc(existing.resolved_at)
        row = ResolutionRow(result_id=result_id, resolved_by=user.username, resolved_at=utcnow())
        s.add(row)
        s.flush()
        _audit(s, "resolved", result_id, {"result_id": result_id, "resolved_by": row.resolved_by,
                                          "row_sha256": _sha(_resolution_content(row))})
        return _utc(row.resolved_at)


# ---- reading ---------------------------------------------------------------------------------

def _latest_result_feedback(s: Session, result_ids: Optional[list[str]] = None) -> dict[str, FeedbackRow]:
    """{result_id: its latest target="result" feedback row}. Append-only table: the newest row counts."""
    q = select(FeedbackRow).where(FeedbackRow.target == "result")
    if result_ids is not None:
        if not result_ids:
            return {}
        q = q.where(FeedbackRow.result_id.in_(result_ids))
    latest: dict[str, FeedbackRow] = {}
    for fb in s.execute(q.order_by(FeedbackRow.created_at, FeedbackRow.id)).scalars():
        latest[fb.result_id] = fb
    return latest


def history(user: User, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    """Saved results, newest first, with the latest verdict and resolution. An employee sees only their own."""
    with get_session() as s:
        q = select(AnalysisResultRow).order_by(AnalysisResultRow.created_at.desc(), AnalysisResultRow.result_id)
        if user.role == "employee":
            q = q.where(AnalysisResultRow.user_id == user.username)
        rows = list(s.execute(q.limit(limit).offset(offset)).scalars())
        ids = [r.result_id for r in rows]
        latest = _latest_result_feedback(s, ids)
        resolved = {r.result_id: r for r in
                    s.execute(select(ResolutionRow).where(ResolutionRow.result_id.in_(ids))).scalars()} if ids else {}
        out = []
        for r in rows:
            fb = latest.get(r.result_id)
            res = resolved.get(r.result_id)
            out.append({"result_id": r.result_id, "created_at": _iso(r.created_at),
                        "subject": (r.ticket_masked or {}).get("subject"), "outcome": r.outcome,
                        "confidence": r.confidence, "latest_verdict": fb.verdict if fb else None,
                        "resolved_at": _iso(res.resolved_at) if res else None,
                        "reviewer": fb.reviewer_id if fb else None})
        return out


def stats() -> dict[str, Any]:
    """Computed live from the tables: outcome mix, useful rate per category, counts, measured MTTR."""
    with get_session() as s:
        outcome_counts = {o: n for o, n in s.execute(
            select(AnalysisResultRow.outcome, func.count()).group_by(AnalysisResultRow.outcome))}
        category = {rid: ((c or {}).get("category") or {}).get("label") or "unclassified"
                    for rid, c in s.execute(select(AnalysisResultRow.result_id, AnalysisResultRow.classification))}
        by_cat: dict[str, Counter] = defaultdict(Counter)
        for rid, fb in _latest_result_feedback(s).items():
            by_cat[category.get(rid, "unclassified")][fb.verdict] += 1
        useful = {}
        for cat, c in sorted(by_cat.items()):
            rated = sum(c.values())
            useful[cat] = {**{v: c[v] for v in VERDICTS}, "rated": rated,
                           "useful_rate": round(c["useful"] / rated, 4) if rated else None}
        minutes = [(_utc(res.resolved_at) - _utc(created)).total_seconds() / 60 for res, created in s.execute(
            select(ResolutionRow, AnalysisResultRow.created_at).join(
                AnalysisResultRow, AnalysisResultRow.result_id == ResolutionRow.result_id))]
        return {
            "n_results": len(category),
            "n_feedback": s.execute(select(func.count()).select_from(FeedbackRow)).scalar_one(),
            "outcome_counts": outcome_counts,
            "useful_rate_by_category": useful,
            "mttr": {"resolved_count": len(minutes),
                     "median_minutes": round(statistics.median(minutes), 2) if minutes else None},
        }


def feedback_counts(ticket_ids: Iterable[str]) -> dict[str, dict[str, int]]:
    """{ticket_id: {"useful": n, "incorrect": n, "unresolved": n}} from target="match" feedback. When one reviewer
    rated the same match on the same result more than once, only their latest verdict counts."""
    ids = [t for t in dict.fromkeys(ticket_ids) if t]
    if not ids:
        return {}
    with get_session() as s:
        latest: dict[tuple[str, str, str], str] = {}
        q = (select(FeedbackRow).where(FeedbackRow.target == "match", FeedbackRow.target_id.in_(ids))
             .order_by(FeedbackRow.created_at, FeedbackRow.id))
        for fb in s.execute(q).scalars():
            latest[(fb.target_id, fb.result_id, fb.reviewer_id)] = fb.verdict
    out: dict[str, dict[str, int]] = {}
    for (tid, _, _), verdict in latest.items():
        out.setdefault(tid, {v: 0 for v in VERDICTS})[verdict] += 1
    return out
