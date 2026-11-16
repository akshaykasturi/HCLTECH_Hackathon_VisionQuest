"""Tamper-evident audit log for pipeline results and reviewer feedback (SQLite, standard library only).

What it stores
- One "result" event per result (the whole Result object as JSON, append-only).
- One "feedback" event per reviewer verdict (confirm / reject / annotate), linked to a result.
  New feedback is added next to old feedback; nothing is overwritten.

How it protects the data
1. Mask first. Every string goes through redact.mask_text() (tokens, API keys, emails, IPs), and
   values under sensitive keys (Authorization, X-Api-Key, password, ...) become "<redacted>".
   This happens BEFORE hashing and BEFORE saving, so a secret never reaches the database.
2. Canonical JSON. Sorted keys, fixed separators, no NaN: the same data always gives the same bytes.
3. Hash chain. Each event stores the SHA-256 of the previous event (prev_hash) and its own SHA-256
   (hash) over its fields + data + prev_hash. The first event links to 64 zeros.
4. One transaction per append (BEGIN IMMEDIATE): read the last hash, insert, commit. Any error rolls
   back, so there are no half-written events and two writers cannot fork the chain.
5. Triggers refuse UPDATE and DELETE on events, so normal code cannot change history by accident.

Limitations (hash chains DETECT some tampering; they do not PREVENT it)
- Anyone who can write the database file can drop the triggers, rewrite events and recompute every
  hash. verify_chain() then says "valid". Only a head hash kept somewhere else (pass it as
  expected_head) catches a rewritten chain or events deleted from the end.
- The data is not encrypted and the file has no access control of its own.
- Masking is pattern-based: secrets in an unknown format can slip through. Strings that merely look
  like an IP (for example a 4-part version number "1.2.3.4") are masked too.
- Timestamps come from this machine's clock and are not independently trusted.
- One SQLite file is meant for one app instance (fine for the demo, not for many servers).

A "result" is whatever our pipeline produces for one case (shape decided in docs/PLAN.md). This module
does not define it: it accepts a dict or a Pydantic model, requires only result_id and case_id, and
stores every other field unchanged apart from masking. list_results() shows `outcome` and `summary`
when the result has them.

Database file: AUDIT_DB environment variable, default backend/data/audit/audit.db (git-ignored).
The folder is created automatically on first use.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from pydantic import BaseModel

from app.common.paths import DATA_DIR
from app.common.redact import mask_text

logger = logging.getLogger(__name__)


def scrub(text: str) -> str:
    """Masked copy of one string (tokens, API keys, emails, IPs) via redact.mask_text; counts are dropped."""
    return mask_text(text)[0]

SCHEMA_VERSION = "1"
GENESIS_HASH = "0" * 64
EVENT_RESULT = "result"
EVENT_FEEDBACK = "feedback"
EVENT_TYPES = (EVENT_RESULT, EVENT_FEEDBACK)
VERDICTS = ("confirm", "reject", "annotate")
REDACTED = "<redacted>"

MAX_LIMIT = 1000
MAX_NOTE_CHARS = 2000
MAX_DATA_BYTES = 5_000_000

# Compared after lower-casing and removing "-" and "_", so "X-Api-Key" and "x_api_key" both match.
# Exact names only: "token_age_s" is a useful number and is kept.
_SENSITIVE_KEYS = {
    "authorization", "proxyauthorization", "xapikey", "apikey", "password", "passwd", "secret",
    "clientsecret", "token", "accesstoken", "refreshtoken", "idtoken", "bearer", "cookie",
    "setcookie", "privatekey", "idempotencykey",
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{0,199}$")
_CSV_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

PathLike = Union[str, Path]


def default_db_path() -> Path:
    """AUDIT_DB if set, else backend/data/audit/audit.db."""
    configured = os.getenv("AUDIT_DB", "").strip()
    return Path(configured) if configured else DATA_DIR / "audit" / "audit.db"

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS audit_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    seq          INTEGER PRIMARY KEY,
    event_id     TEXT NOT NULL UNIQUE,
    ts           TEXT NOT NULL,
    event_type   TEXT NOT NULL CHECK (event_type IN ('{EVENT_RESULT}', '{EVENT_FEEDBACK}')),
    case_id      TEXT NOT NULL,
    result_id TEXT NOT NULL,
    request_id   TEXT,
    data_json    TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL UNIQUE
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_audit_one_result
    ON audit_events (result_id) WHERE event_type = '{EVENT_RESULT}';
CREATE INDEX IF NOT EXISTS ix_audit_case ON audit_events (case_id);
CREATE INDEX IF NOT EXISTS ix_audit_result ON audit_events (result_id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
"""

_COLUMNS = ("seq", "event_id", "ts", "event_type", "case_id", "result_id", "request_id",
            "data_json", "prev_hash", "hash")


# ---------- errors ----------

class AuditError(Exception):
    """Base class for every audit error."""


class AuditValidationError(AuditError, ValueError):
    """The caller passed bad input (unknown verdict, bad id, data that is not JSON-safe...)."""


class AuditNotFoundError(AuditError, LookupError):
    """The result asked for does not exist."""


class AuditStorageError(AuditError):
    """The database could not be opened, read or written. The transaction was rolled back."""


# ---------- masking and canonical JSON ----------

def _is_sensitive_key(key: str) -> bool:
    return key.lower().replace("-", "").replace("_", "") in _SENSITIVE_KEYS


def sanitize(value: Any) -> Any:
    """Return a JSON-safe, masked copy of value: strings scrubbed, sensitive keys redacted.

    Pydantic models, datetimes, dates, enums, tuples and sets are converted; anything else that
    JSON cannot hold (objects, NaN, infinity, non-string keys) raises AuditValidationError.
    """
    if isinstance(value, BaseModel):
        return sanitize(value.model_dump(mode="json"))
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AuditValidationError("NaN and infinity cannot be stored")
        return value
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, Enum):
        return sanitize(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
            return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return value.isoformat(timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise AuditValidationError("dictionary keys must be strings")
            clean_key = scrub(key)
            if _is_sensitive_key(key):
                out[clean_key] = None if item is None else REDACTED
            else:
                out[clean_key] = sanitize(item)
        return out
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((sanitize(item) for item in value), key=canonical_json)
    raise AuditValidationError(f"cannot store a value of type {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Serialise already-sanitized data deterministically (sorted keys, no spaces, no NaN)."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise AuditValidationError(f"data is not JSON-serialisable: {exc}") from None


def compute_hash(seq: int, event_id: str, ts: str, event_type: str, case_id: str,
                 result_id: str, request_id: Optional[str], data_json: str, prev_hash: str) -> str:
    """SHA-256 (hex) over the canonical JSON of one event's fields, its data and prev_hash."""
    payload = canonical_json({
        "seq": seq, "event_id": event_id, "ts": ts, "event_type": event_type,
        "case_id": case_id, "result_id": result_id, "request_id": request_id,
        "data": json.loads(data_json), "prev_hash": prev_hash,
    })
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------- validation helpers ----------

def _check_id(name: str, value: Any, required: bool = True) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise AuditValidationError(
            f"{name} must be 1-200 characters: letters, digits and . _ : / # - (got {type(value).__name__})")
    return value


def _check_limit(limit: Any, offset: Any) -> None:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise AuditValidationError(f"limit must be an integer from 1 to {MAX_LIMIT}")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise AuditValidationError("offset must be an integer of 0 or more")


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ---------- database plumbing ----------

def _connect(db_path: PathLike) -> sqlite3.Connection:
    if not isinstance(db_path, (str, Path)) or not str(db_path).strip():
        raise AuditValidationError("db_path must be a non-empty path")
    path = Path(db_path)
    # Git does not keep empty folders, so on a fresh clone data/audit/ is missing: create it here,
    # on every open, so no caller has to remember to (requests work even if startup did not run).
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # e.g. a file sits where the folder should be, or no permission
        raise AuditStorageError(
            f"cannot create the folder for the audit database ({exc.__class__.__name__}): {path.parent}") from None
    try:
        conn = sqlite3.connect(str(path), isolation_level=None, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    except sqlite3.Error as exc:
        raise AuditStorageError(f"cannot open audit database: {exc}") from None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO audit_meta (key, value) VALUES ('schema_version', ?)",
                     (SCHEMA_VERSION,))
        row = conn.execute("SELECT value FROM audit_meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.DatabaseError as exc:
        raise AuditStorageError(f"cannot prepare audit database: {exc}") from None
    if row["value"] != SCHEMA_VERSION:
        raise AuditStorageError(
            f"audit database schema version {row['value']} is not supported (expected {SCHEMA_VERSION})")


def _open(db_path: PathLike) -> sqlite3.Connection:
    conn = _connect(db_path)
    try:
        _ensure_schema(conn)
    except Exception:
        conn.close()
        raise
    return conn


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    event = {col: row[col] for col in _COLUMNS if col != "data_json"}
    event["data"] = json.loads(row["data_json"])
    return event


def _append(db_path: PathLike, event_type: str, case_id: str, result_id: str,
            request_id: Optional[str], data: dict[str, Any],
            must_exist_result: bool = False) -> dict[str, Any]:
    """Mask, serialise, link and insert one event inside a single IMMEDIATE transaction."""
    clean = sanitize(data)
    data_json = canonical_json(clean)
    if len(data_json.encode("utf-8")) > MAX_DATA_BYTES:
        raise AuditValidationError(f"event data is larger than {MAX_DATA_BYTES} bytes")

    with closing(_open(db_path)) as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if must_exist_result:
                found = conn.execute(
                    "SELECT case_id FROM audit_events WHERE event_type = ? AND result_id = ?",
                    (EVENT_RESULT, result_id)).fetchone()
                if found is None:
                    raise AuditNotFoundError(f"result not found: {result_id}")
                case_id = found["case_id"]
            elif conn.execute(
                    "SELECT 1 FROM audit_events WHERE event_type = ? AND result_id = ?",
                    (EVENT_RESULT, result_id)).fetchone() is not None:
                raise AuditValidationError(
                    f"result {result_id} is already recorded (the audit log is append-only)")

            last = conn.execute("SELECT seq, hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            seq = 1 if last is None else last["seq"] + 1
            prev_hash = GENESIS_HASH if last is None else last["hash"]
            event_id = uuid.uuid4().hex
            ts = _now_utc()
            digest = compute_hash(seq, event_id, ts, event_type, case_id, result_id, request_id,
                                  data_json, prev_hash)
            conn.execute(
                f"INSERT INTO audit_events ({', '.join(_COLUMNS)}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (seq, event_id, ts, event_type, case_id, result_id, request_id, data_json,
                 prev_hash, digest))
            conn.execute("COMMIT")
        except AuditError:
            conn.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            logger.warning("audit append failed and was rolled back (%s, seq not written)", event_type)
            raise AuditStorageError(f"audit event was not saved: {exc}") from None

    logger.info("audit event %s #%d saved (result %s)", event_type, seq, result_id)
    return {"seq": seq, "event_id": event_id, "ts": ts, "event_type": event_type,
            "case_id": case_id, "result_id": result_id, "request_id": request_id,
            "data": clean, "prev_hash": prev_hash, "hash": digest}


def _read(db_path: PathLike, sql: str, params: tuple) -> list[sqlite3.Row]:
    with closing(_open(db_path)) as conn:
        try:
            return conn.execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            raise AuditStorageError(f"cannot read audit database: {exc}") from None


# ---------- public API ----------

def init_db(db_path: PathLike) -> None:
    """Create the audit tables, indexes and append-only triggers if missing (safe to call again)."""
    with closing(_open(db_path)):
        pass


def record_result(db_path: PathLike, result: Union[dict[str, Any], BaseModel],
                     request_id: Optional[str] = None) -> dict[str, Any]:
    """Append one Result (dict or Pydantic model) as a masked event; returns the stored event.

    Needs result_id and case_id; every other field is kept as given (after masking).
    The same result_id cannot be recorded twice.
    """
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    if not isinstance(result, dict):
        raise AuditValidationError("result must be a dict or a Pydantic model")
    result_id = _check_id("result_id", result.get("result_id"))
    case_id = _check_id("case_id", result.get("case_id"))
    request_id = _check_id("request_id", request_id, required=False)
    return _append(db_path, EVENT_RESULT, case_id, result_id, request_id, result)


def get_result(db_path: PathLike, result_id: str) -> Optional[dict[str, Any]]:
    """The stored (masked) Result for this id, or None if it was never recorded."""
    _check_id("result_id", result_id)
    rows = _read(db_path, "SELECT data_json FROM audit_events WHERE event_type = ? AND result_id = ?",
                 (EVENT_RESULT, result_id))
    return json.loads(rows[0]["data_json"]) if rows else None


def list_results(db_path: PathLike, case_id: Optional[str] = None, limit: int = 50,
                   offset: int = 0) -> list[dict[str, Any]]:
    """Short summaries of recorded results, newest first, optionally for one case."""
    _check_id("case_id", case_id, required=False)
    _check_limit(limit, offset)
    sql = ("SELECT e.seq, e.ts, e.case_id, e.result_id, e.request_id, e.data_json, "
           "(SELECT COUNT(*) FROM audit_events f WHERE f.event_type = ? AND f.result_id = e.result_id) "
           "AS feedback_count FROM audit_events e WHERE e.event_type = ?")
    params: list[Any] = [EVENT_FEEDBACK, EVENT_RESULT]
    if case_id is not None:
        sql += " AND e.case_id = ?"
        params.append(case_id)
    sql += " ORDER BY e.seq DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    out = []
    for row in _read(db_path, sql, tuple(params)):
        data = json.loads(row["data_json"])
        out.append({"result_id": row["result_id"], "case_id": row["case_id"],
                    "recorded_at": row["ts"], "request_id": row["request_id"],
                    "outcome": data.get("outcome"), "summary": data.get("summary"),
                    "feedback_count": row["feedback_count"]})
    return out


def record_feedback(db_path: PathLike, result_id: str, verdict: str, item_id: Optional[str] = None,
                    note: Optional[str] = None, request_id: Optional[str] = None) -> dict[str, Any]:
    """Append one reviewer verdict (confirm / reject / annotate) for an existing result.

    Earlier feedback is kept. "annotate" needs a note. Returns the stored feedback record.
    """
    _check_id("result_id", result_id)
    if verdict not in VERDICTS:
        raise AuditValidationError(f"verdict must be one of {', '.join(VERDICTS)}")
    _check_id("item_id", item_id, required=False)
    request_id = _check_id("request_id", request_id, required=False)
    if note is not None:
        if not isinstance(note, str):
            raise AuditValidationError("note must be text")
        note = note.strip() or None
        if note is not None and len(note) > MAX_NOTE_CHARS:
            raise AuditValidationError(f"note is longer than {MAX_NOTE_CHARS} characters")
    if verdict == "annotate" and note is None:
        raise AuditValidationError("an annotate verdict needs a note")

    data = {"verdict": verdict, "item_id": item_id, "note": note}
    event = _append(db_path, EVENT_FEEDBACK, "", result_id, request_id, data,
                    must_exist_result=True)
    return _feedback_from_event(event)


def _feedback_from_event(event: dict[str, Any]) -> dict[str, Any]:
    data = event["data"]
    return {"feedback_id": event["event_id"], "result_id": event["result_id"],
            "case_id": event["case_id"], "verdict": data["verdict"], "item_id": data["item_id"],
            "note": data["note"], "created_at": event["ts"], "request_id": event["request_id"]}


def get_feedback(db_path: PathLike, result_id: str) -> list[dict[str, Any]]:
    """All feedback for one result, oldest first. Raises AuditNotFoundError if unknown."""
    if get_result(db_path, result_id) is None:
        raise AuditNotFoundError(f"result not found: {result_id}")
    rows = _read(db_path, f"SELECT {', '.join(_COLUMNS)} FROM audit_events "
                          "WHERE event_type = ? AND result_id = ? ORDER BY seq",
                 (EVENT_FEEDBACK, result_id))
    return [_feedback_from_event(_row_to_event(row)) for row in rows]


def list_events(db_path: PathLike, case_id: Optional[str] = None, result_id: Optional[str] = None,
                event_type: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """Audit events in chronological (chain) order, with optional filters."""
    _check_id("case_id", case_id, required=False)
    _check_id("result_id", result_id, required=False)
    if event_type is not None and event_type not in EVENT_TYPES:
        raise AuditValidationError(f"event_type must be one of {', '.join(EVENT_TYPES)}")
    _check_limit(limit, offset)
    sql = f"SELECT {', '.join(_COLUMNS)} FROM audit_events WHERE 1 = 1"
    params: list[Any] = []
    for column, value in (("case_id", case_id), ("result_id", result_id), ("event_type", event_type)):
        if value is not None:
            sql += f" AND {column} = ?"
            params.append(value)
    sql += " ORDER BY seq LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_to_event(row) for row in _read(db_path, sql, tuple(params))]


def verify_chain(db_path: PathLike, expected_head: Optional[str] = None) -> dict[str, Any]:
    """Recalculate every hash and link, oldest first, and report the first problem found.

    Returns {valid, events_checked, head_hash, first_bad_seq, reason}. Pass expected_head (a head
    hash saved elsewhere) to also catch events removed from the end or a fully rewritten chain.
    """
    rows = _read(db_path, f"SELECT {', '.join(_COLUMNS)} FROM audit_events ORDER BY seq", ())
    prev_hash = GENESIS_HASH
    checked = 0

    def bad(seq: Optional[int], reason: str) -> dict[str, Any]:
        logger.warning("audit chain check failed at event %s: %s", seq, reason)
        return {"valid": False, "events_checked": checked, "head_hash": prev_hash,
                "first_bad_seq": seq, "reason": reason}

    for expected_seq, row in enumerate(rows, start=1):
        seq = row["seq"]
        if seq != expected_seq:
            return bad(expected_seq, f"event #{expected_seq} is missing (next event is #{seq})")
        if row["event_type"] not in EVENT_TYPES:
            return bad(seq, "unknown event type")
        if row["prev_hash"] != prev_hash:
            return bad(seq, "prev_hash does not match the previous event's hash")
        try:
            data = json.loads(row["data_json"])
        except (TypeError, ValueError):
            return bad(seq, "event data is not valid JSON")
        if canonical_json(data) != row["data_json"]:
            return bad(seq, "event data is not in canonical form (edited outside this module)")
        recomputed = compute_hash(seq, row["event_id"], row["ts"], row["event_type"], row["case_id"],
                                  row["result_id"], row["request_id"], row["data_json"], prev_hash)
        if recomputed != row["hash"]:
            return bad(seq, "hash does not match the event's contents")
        prev_hash = row["hash"]
        checked += 1

    if expected_head is not None and expected_head != prev_hash:
        return bad(None, "head hash differs from expected_head (events removed from the end, "
                         "or the chain was replaced)")
    return {"valid": True, "events_checked": checked, "head_hash": prev_hash,
            "first_bad_seq": None, "reason": None}


def _csv_cell(value: Any) -> Any:
    """Stop spreadsheet formula injection: a text cell starting with = + - @ gets a leading quote."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_START):
        return "'" + value
    return value


def export_csv(db_path: PathLike) -> str:
    """All events as CSV text, oldest first. Data is masked again on the way out, as a safety net."""
    rows = _read(db_path, f"SELECT {', '.join(_COLUMNS)} FROM audit_events ORDER BY seq", ())
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_COLUMNS)
    for row in rows:
        values = [row[col] for col in _COLUMNS]
        values[_COLUMNS.index("data_json")] = scrub(row["data_json"])
        writer.writerow([_csv_cell(v) for v in values])
    return buffer.getvalue()
