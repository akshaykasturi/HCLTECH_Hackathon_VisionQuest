"""Raw-to-clean ingestion: one decision per raw ITSM record (docs/INGESTION.md).

    decisions = ingest(raw_rows, Taxonomy.from_curated(curated_rows))

Every raw record gets exactly one decision; nothing is dropped silently:

    ACCEPT           already clean
    NORMALIZE        recoverable defects fixed with the contract (app/ingest/contract.py); flags kept
    MERGE_DUPLICATE  the same incident submitted again; points at the canonical record, not indexed twice
    QUARANTINE       conflicting, invalid or junk: held for a human, never auto-corrected
    HOLD_SECURITY    the security screen (app/tickets/screen.py) flagged it: never indexed, routed to security

Steps, in order: junk -> timestamps -> security screen -> field normalisation -> taxonomy (category rule) ->
priority -> source-reference collisions -> duplicates -> redaction and clarify flags.

Privacy: decisions carry record IDs, reason codes and field names only. Ticket text never goes into a reason,
a log line or a report. Requester contact fields are dropped from the clean record (not needed downstream).
Text stays unmasked in the clean record, like the curated tickets: masking happens before search, storage, logs
and display (tickets/guard.py, common/redact.py); `must_redact` says what is in it.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.common.redact import mask_text
from app.ingest.contract import (CATEGORY_ALIASES, CHANNEL_ALIASES, ENV_ALIASES, MATRIX, PRIORITY_ALIASES, REF_FORMAT,
                                 STATUS_ALIASES, SUBCATEGORY_ALIASES, TEAM_ALIASES)
from app.ingest.normalize import canon, clean_text, parse_ts

ACCEPT, NORMALIZE, MERGE, QUARANTINE, HOLD = "ACCEPT", "NORMALIZE", "MERGE_DUPLICATE", "QUARANTINE", "HOLD_SECURITY"

# Fields of a clean record: the curated tickets.csv names (docs/PLAN.md data contract) plus the source keys.
CLEAN_FIELDS = ("source_record_id", "source_system", "source_ticket_ref", "created_at", "resolved_at", "status",
                "channel", "category", "subcategory", "priority", "assigned_team", "affected_service", "environment",
                "subject", "description", "root_cause", "resolution_notes", "resolution_steps")
CANONICAL_TS_SOURCE = "ServiceNow-EMEA"   # its convention (UTC, no zone) is already the canonical one
DUPLICATE_WINDOW = timedelta(hours=2)
_PREFIX = re.compile(r"^\s*(?:(?:fw|fwd|re)\s*:\s*|urgent\s*-\s*)+", re.I)
_LOGIN = re.compile(r"\bmy (?:login|username|user name) is\b", re.I)
_WORD = re.compile(r"[a-z0-9]+")

# Junk patterns: whole-record rules, checked on the cleaned subject (lower case) and description.
_TEST = re.compile(r"^(?:test\b|asdf\b)")
_AUTO_REPLY = re.compile(r"^automatic reply\s*:")
_BOUNCE = re.compile(r"^undeliverable\s*:")
_HEARTBEAT = re.compile(r"^auto\s*:\s*heartbeat\b")
_PLACEHOLDER = {"n/a", "na", "-", "none", "null", "tbd"}
# Flags about a VALUE (missing, corrected or unreadable) make a record NORMALIZE. Review flags
# (needs_clarification, source_ref_shared) are notes for a person and leave an otherwise clean record ACCEPT.
VALUE_FLAGS = {"label_untrusted", "unrecognised_value", "subcategory_needs_review", "missing_description"}


@dataclass(frozen=True)
class Taxonomy:
    """Reference data learned from the curated tickets: which category/subcategory/team each service has."""
    by_service: dict[str, tuple[tuple[str, str, str], ...]]   # service -> ((category, subcategory, team), ...)
    teams: tuple[str, ...]
    subcategories: tuple[str, ...]

    @classmethod
    def from_curated(cls, rows: Iterable[dict[str, Any]]) -> "Taxonomy":
        svc: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        teams, subs = set(), set()
        for r in rows:
            if r.get("affected_service") and r.get("category") and r.get("subcategory") and r.get("assigned_team"):
                svc[r["affected_service"]].add((r["category"], r["subcategory"], r["assigned_team"]))
                teams.add(r["assigned_team"])
                subs.add(r["subcategory"])
        return cls({k: tuple(sorted(v)) for k, v in svc.items()}, tuple(sorted(teams)), tuple(sorted(subs)))

    def families(self, service: Optional[str]) -> tuple[tuple[str, str, str], ...]:
        if not service:
            return ()
        key = service.strip().lower()
        return next((v for k, v in self.by_service.items() if k.lower() == key), ())

    def service_name(self, service: Optional[str]) -> Optional[str]:
        key = (service or "").strip().lower()
        return next((k for k in self.by_service if k.lower() == key), None)


@dataclass
class Decision:
    source_record_id: str
    action: str = ACCEPT
    reasons: list[str] = field(default_factory=list)       # quarantine / hold codes
    changes: list[str] = field(default_factory=list)       # fields the pipeline normalised
    flags: list[str] = field(default_factory=list)         # e.g. label_untrusted, needs_clarification
    must_redact: list[str] = field(default_factory=list)   # kinds of personal data / secrets found in the text
    canonical_id: Optional[str] = None                     # MERGE_DUPLICATE: the record it duplicates
    clean: Optional[dict[str, Any]] = None                 # the clean record (None for junk / security holds)

    @property
    def label_trusted(self) -> bool:
        return "label_untrusted" not in self.flags

    @property
    def indexable(self) -> bool:
        """May go into the similar-ticket index: accepted or normalised, nothing held back."""
        return self.action in (ACCEPT, NORMALIZE)


# ---------------------------------------------------------------- steps (pure functions, each testable)

def junk_reasons(subject: str, description: str) -> list[str]:
    """Records that are not incidents or cannot be read. Quarantined, never dropped."""
    s, d = subject.lower(), description.lower()
    if not s and not d:
        return ["no_subject_or_description"]
    if s in _PLACEHOLDER and d in _PLACEHOLDER | {""}:
        return ["placeholder_values_only"]
    if "�" in subject + description or "ï¿½" in subject + description:
        return ["unrecoverable_encoding"]
    if _AUTO_REPLY.match(s):
        return ["auto_reply_not_incident"]
    if _BOUNCE.match(s):
        return ["bounce_not_incident"]
    if _HEARTBEAT.match(s):
        return ["non_incident_event"]
    if _TEST.match(s):
        return ["test_submission"]
    if "[truncated" in d:
        return ["truncated_text"]
    return []


def timestamp_reasons(created: Optional[datetime], resolved: Optional[datetime], raw_resolved: str,
                      status: Optional[str], now: datetime) -> list[str]:
    reasons = []
    if created is None:
        return ["invalid_timestamp"]
    if created > now + timedelta(days=1):
        reasons.append("timestamp_in_future")
    if raw_resolved.strip() and resolved is None:
        reasons.append("invalid_timestamp")
    if resolved is not None and resolved < created:
        reasons.append("resolved_before_created")
    if status in ("resolved", "closed") and resolved is None and not raw_resolved.strip():
        reasons.append("closed_without_resolved_at")
    if status in ("open", "in_progress") and resolved is not None:
        reasons.append("open_with_resolved_at")
    return reasons


def _level(value: str) -> Optional[int]:
    m = re.match(r"\s*([123])\b", value or "")
    return int(m.group(1)) if m else None


def resolve_priority(raw_priority: str, impact: str, urgency: str) -> tuple[Optional[str], list[str]]:
    """(priority, reasons). Stated priority wins if it agrees with impact x urgency; a missing one is derived
    from the matrix; a stated one that contradicts the matrix is a conflict for a human."""
    stated = canon(raw_priority, PRIORITY_ALIASES) if (raw_priority or "").strip() else None
    cell = (_level(impact), _level(urgency))
    derived = next((p for p, cells in MATRIX.items() if cell in cells), None) if None not in cell else None
    if stated and derived and stated != derived:
        return stated, ["priority_matrix_conflict"]
    if stated:
        return stated, []
    if derived:
        return derived, []
    return None, ([] if not (raw_priority or "").strip() else ["unknown_priority"])


def reconcile_taxonomy(tax: Taxonomy, raw: dict[str, str]) -> tuple[dict[str, Optional[str]], list[str], list[str]]:
    """(category, subcategory, assigned_team, affected_service), reasons, flags.

    The documented category rule (docs/DATA-CARD.md): when the category disagrees with the affected service but
    the resolver team agrees (or is blank), trust the service; when category AND team both disagree, quarantine.
    """
    service = tax.service_name(raw.get("affected_service")) or ((raw.get("affected_service") or "").strip() or None)
    families = tax.families(service)
    cat = canon(raw.get("category", ""), CATEGORY_ALIASES) if (raw.get("category") or "").strip() else None
    sub_table = {s: SUBCATEGORY_ALIASES.get(s, []) for s in tax.subcategories}
    sub = canon(raw.get("subcategory", ""), sub_table) if (raw.get("subcategory") or "").strip() else None
    team_table = {t: TEAM_ALIASES.get(t, []) for t in tax.teams}
    team = canon(raw.get("assigned_team", ""), team_table) if (raw.get("assigned_team") or "").strip() else None
    flags: list[str] = []

    if not families:   # unknown or missing service: keep what we can read, never guess
        if not (cat and sub and team):
            flags.append("label_untrusted")
        return {"category": cat, "subcategory": sub, "assigned_team": team, "affected_service": service}, [], flags

    fam_cats = {f[0] for f in families}
    fam_teams = {f[2] for f in families}
    if cat and cat not in fam_cats and team and team not in fam_teams:
        return {"category": cat, "subcategory": sub, "assigned_team": team, "affected_service": service}, \
               ["category_service_conflict"], ["label_untrusted"]

    chosen = [f for f in families if f[1] == sub] if len(families) > 1 else list(families)
    if len(chosen) != 1:
        # The service maps to several subcategories and the record does not say which: keep the category
        # (shared by all of them) and leave the subcategory for review.
        shared = fam_cats.pop() if len(fam_cats) == 1 else None
        return {"category": shared, "subcategory": None, "assigned_team": team if team in fam_teams else None,
                "affected_service": service}, [], ["label_untrusted", "subcategory_needs_review"]
    f_cat, f_sub, f_team = chosen[0]
    if cat != f_cat or sub != f_sub:
        flags.append("label_untrusted")   # the raw label was missing or wrong: never classifier ground truth
    return {"category": f_cat, "subcategory": f_sub, "assigned_team": f_team, "affected_service": service}, [], flags


def redaction_kinds(text: str) -> list[str]:
    """Which kinds of personal data or secrets are in the text (from common/redact.py), plus user names."""
    _, counts = mask_text(text)
    kinds = {k for k, n in counts.items() if n}
    if _LOGIN.search(text):
        kinds.add("username")
    return sorted(kinds)


def description_too_thin(subject: str, description: str) -> bool:
    """Missing, under 3 words, or only repeating the subject (an email body that copies the title adds nothing)."""
    words = _WORD.findall(description.lower())
    return len(words) < 3 or words == _WORD.findall(subject.lower())


def shared_ref_owners(rows: Iterable[dict[str, str]]) -> dict[str, str]:
    """{shared reference: the one record that may keep it}. Only when exactly one holder's number has its own
    source system's form (contract.REF_FORMAT); a reference with no such single holder is not in the result."""
    holders: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        if (ref := (r.get("source_ticket_ref") or "").strip()):
            holders[ref].append(r)
    owners = {}
    for ref, rs in holders.items():
        if len(rs) < 2:
            continue
        fits = [r for r in rs if (p := REF_FORMAT.get(r.get("source_system", ""))) and re.fullmatch(p, ref)]
        if len(fits) == 1:
            owners[ref] = fits[0]["source_record_id"]
    return owners


def subject_key(subject: str) -> str:
    """Duplicate key: no FW:/Re:/URGENT prefix, no 'multiple users' tail, words only."""
    s = _PREFIX.sub("", subject.lower()).replace(" - multiple users", "")
    return " ".join(_WORD.findall(s))


# ---------------------------------------------------------------- the pipeline

def _screen_codes(subject: str, description: str, service: Optional[str]) -> list[str]:
    from app.tickets.models import TicketIn
    from app.tickets.screen import screen
    ticket = TicketIn(subject=(subject or "(none)")[:300].ljust(3, "."), description=(description or subject)[:5000] or "-",
                      affected_service=(service or None) and service[:100])
    result = screen(ticket)
    return list(result.codes) if (result.blocked or result.safety) else []


def ingest(rows: list[dict[str, str]], taxonomy: Taxonomy, now: Optional[datetime] = None,
           screen_codes=_screen_codes) -> list[Decision]:
    """One Decision per raw row, in the same order."""
    now = now or datetime.now(timezone.utc)
    decisions: list[Decision] = []
    ref_counts = Counter((r.get("source_ticket_ref") or "").strip() for r in rows)
    ref_owner = shared_ref_owners(rows)
    candidates: list[tuple[Decision, datetime, dict[str, str]]] = []

    for raw in rows:
        d = Decision(source_record_id=raw["source_record_id"])
        decisions.append(d)
        system = raw.get("source_system", "")
        subject, description = clean_text(raw.get("subject", "")), clean_text(raw.get("description", ""))
        d.changes += [f for f, v, c in (("subject", raw.get("subject", ""), subject),
                                        ("description", raw.get("description", ""), description)) if v != c]

        # 1. junk
        if (reasons := junk_reasons(subject, description)):
            d.action, d.reasons = QUARANTINE, reasons
            continue

        # 2. timestamps (source convention -> UTC)
        status = canon(raw.get("status", ""), STATUS_ALIASES)
        created = parse_ts(raw.get("created_at", ""), system)
        resolved = parse_ts(raw.get("resolved_at", ""), system)
        ts_reasons = timestamp_reasons(created, resolved, raw.get("resolved_at", ""), status, now)

        # 3. security screen (raw text, in memory only: only codes come back)
        codes = _screen_codes_safe(screen_codes, subject, description, raw.get("affected_service"))
        if codes:
            d.action, d.reasons = HOLD, codes
            continue

        # 4-6. fields, taxonomy, priority
        fields, tax_reasons, d.flags = reconcile_taxonomy(taxonomy, raw)
        priority, prio_reasons = resolve_priority(raw.get("priority", ""), raw.get("impact", ""), raw.get("urgency", ""))
        clean = {
            "source_record_id": d.source_record_id, "source_system": system,
            "source_ticket_ref": raw.get("source_ticket_ref", ""),
            "created_at": created.isoformat(timespec="seconds") if created else "",
            "resolved_at": resolved.isoformat(timespec="seconds") if resolved else "",
            "status": status, "channel": canon(raw.get("channel", ""), CHANNEL_ALIASES),
            "environment": canon(raw.get("environment", ""), ENV_ALIASES), "priority": priority,
            "subject": subject, "description": description,
            **fields,
            **{k: clean_text(raw.get(k, "")) for k in ("root_cause", "resolution_notes", "resolution_steps")},
        }
        d.clean = clean
        for key in ("status", "channel", "environment", "priority", "category", "subcategory", "assigned_team"):
            if (clean.get(key) or "") != (raw.get(key) or ""):
                d.changes.append(key)
        if system != CANONICAL_TS_SOURCE:
            d.changes.append("timestamps")
        if any(clean[k] is None for k in ("status", "channel", "environment")):
            d.flags.append("unrecognised_value")

        # 7. a source reference held by more than one record. Arrival order does not say which one owns it; the
        #    number's own format can: if exactly one holder's number has its own system's form, it keeps the
        #    number and the others are held. Otherwise every holder goes to a human.
        ref = (raw.get("source_ticket_ref") or "").strip()
        reasons = ts_reasons + tax_reasons + prio_reasons
        if ref and ref_counts[ref] > 1:
            if ref_owner.get(ref) == d.source_record_id:
                d.flags.append("source_ref_shared")   # kept, but a human can see another record claimed it
            else:
                reasons.append("source_ref_collision")
        if reasons:
            d.action, d.reasons = QUARANTINE, sorted(set(reasons))
            d.flags.append("label_untrusted")
            continue

        # 9. what the text needs downstream
        d.must_redact = redaction_kinds(f"{subject}\n{description}")
        if not description:
            d.flags.append("missing_description")
        if description_too_thin(subject, description):
            d.flags.append("needs_clarification")
        d.action = NORMALIZE if (d.changes or VALUE_FLAGS & set(d.flags) or d.must_redact) else ACCEPT
        candidates.append((d, created, raw))

    _merge_duplicates(candidates)
    return decisions


def _screen_codes_safe(screen_codes, subject: str, description: str, service: Optional[str]) -> list[str]:
    try:
        return screen_codes(subject, description, service)
    except Exception:   # the screen must not stop ingestion; hold the record so a human looks at it
        return ["SCREEN_ERROR"]


def _overlap(a: str, b: str) -> tuple[bool, float]:
    """How well description a repeats b: (one is a prefix of the other, word overlap 0..1)."""
    la, lb = a.lower().rstrip(". "), b.lower().rstrip(". ")
    wa, wb = set(_WORD.findall(la)), set(_WORD.findall(lb))
    return (bool(la) and bool(lb) and (la.startswith(lb) or lb.startswith(la)),
            len(wa & wb) / len(wa | wb) if wa | wb else 0.0)


def _merge_duplicates(candidates: list[tuple[Decision, datetime, dict[str, str]]]) -> None:
    """The same incident submitted twice: an explicit 'related_ref' link, or a reply/forward (FW:, Re:,
    URGENT -) of a ticket with the same subject and service raised shortly before. Conservative on purpose:
    two different people reporting the same outage are NOT merged (they are separate incidents)."""
    by_ref = {raw.get("source_ticket_ref"): d for d, _, raw in candidates}
    earlier: dict[tuple[str, str], list[tuple[datetime, Decision]]] = defaultdict(list)
    for d, created, raw in sorted(candidates, key=lambda c: c[1]):
        key = (d.clean["affected_service"] or "", subject_key(d.clean["subject"]))
        target: Optional[Decision] = None
        related = (raw.get("related_ref") or "").strip()
        if related and related in by_ref and by_ref[related] is not d:
            target = by_ref[related]
        elif _PREFIX.match(raw.get("subject", "") or ""):
            # several people can report one burst with the same subject: pick the earlier ticket whose
            # description this one repeats best, not simply the latest
            recent = [e for t, e in earlier[key] if timedelta(0) <= created - t <= DUPLICATE_WINDOW]
            target = max(recent, key=lambda e: _overlap(d.clean["description"], e.clean["description"]), default=None)
        if target is not None:
            d.action, d.canonical_id = MERGE, target.source_record_id
        else:
            earlier[key].append((created, d))
