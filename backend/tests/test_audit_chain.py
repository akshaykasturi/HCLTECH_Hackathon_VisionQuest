"""Tests for the SQLite audit log (app/uc4/audit.py). Every test uses its own temporary database.

The secrets below are made up for these tests (JWT-shaped, demo_live_ key, example.com email,
documentation-range IP). They are never printed: assertions only report the leak kinds.
"""
import sqlite3
from contextlib import closing

import pytest
from pydantic import BaseModel

from app.common import audit
from app.common.audit import (
    GENESIS_HASH, AuditNotFoundError, AuditStorageError, AuditValidationError,
    canonical_json, export_csv, get_result, get_feedback, init_db, list_results, list_events,
    record_result, record_feedback, sanitize, verify_chain,
)
from app.common.redact import find_leaks

FAKE_JWT = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJhdWRpdC10ZXN0In0.ZmFrZXNpZw"
FAKE_KEY = "demo_live_AuditTestKey0000000000000001"
FAKE_EMAIL = "reviewer.audit@example.com"
FAKE_IP = "203.0.113.77"
FAKE_VALUES = (FAKE_JWT, FAKE_KEY, FAKE_EMAIL, FAKE_IP)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "audit_test.db"
    init_db(path)
    return path


def make_result(result_id="dx-0001", case_id="CASE-0050", **extra):
    """A result with every field of the pipeline's Result object (values are illustrative)."""
    result = {
        "result_id": result_id,
        "created_at": "2026-11-16T10:00:00.000Z",
        "case_id": case_id,
        "outcome": "RECOMMEND",
        "summary": "Likely answer A, from two pieces of evidence.",
        "confidence": 0.82,
        "items": [
            {"id": "answer-a", "rank": 1, "confidence": 0.71, "supporting": ["ev:1"], "contradicting": []},
            {"id": "answer-b", "rank": 2, "confidence": 0.18, "supporting": [], "contradicting": ["ev:2"]},
        ],
        "evidence": [{"id": "ev:1", "kind": "metric", "text": "p95 2400 ms vs 180 ms baseline",
                      "data": {"p95_ms": 2400.0}}],
        "missing_info": [],
        "report": {"summary": "Likely answer A.", "steps": ["Check the p95 latency."]},
        "report_findings": [],
        "generator": "template",
        "trace": [{"step": "evidence", "ms": 12.5}],
    }
    result.update(extra)
    return result


def raw_db_text(path) -> str:
    """The database file's bytes as text, to check that no secret was written anywhere."""
    return path.read_bytes().decode("latin-1")


def raw_execute(path, *statements):
    """Simulate someone editing the file directly, outside audit.py."""
    with closing(sqlite3.connect(str(path), isolation_level=None)) as conn:
        for sql in statements:
            conn.execute(sql)


# 1. Database initialization
def test_init_creates_tables_and_is_idempotent(db):
    init_db(db)  # second call must not fail or duplicate anything
    with closing(sqlite3.connect(str(db))) as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
        version = conn.execute("SELECT value FROM audit_meta WHERE key='schema_version'").fetchone()[0]
    assert {"audit_events", "audit_meta", "audit_events_no_update", "audit_events_no_delete"} <= names
    assert version == audit.SCHEMA_VERSION
    assert verify_chain(db) == {"valid": True, "events_checked": 0, "head_hash": GENESIS_HASH,
                                "first_bad_seq": None, "reason": None}


def test_missing_folder_is_created_like_a_fresh_clone(tmp_path):
    # Regression: Git does not keep the empty data/audit/ folder, and POST /api/diagnose returned 503.
    path = tmp_path / "data" / "audit" / "uc4_audit.db"
    assert not path.parent.exists()
    init_db(path)
    assert path.parent.is_dir() and path.is_file()
    event = record_result(path, make_result())
    record_feedback(path, "dx-0001", "confirm", item_id="answer-a")
    assert get_result(path, "dx-0001")["case_id"] == "CASE-0050"
    assert [f["verdict"] for f in get_feedback(path, "dx-0001")] == ["confirm"]
    chain = verify_chain(path)
    assert chain["valid"] and chain["events_checked"] == 2 and event["prev_hash"] == GENESIS_HASH


def test_missing_folder_is_created_on_any_call_not_only_init(tmp_path):
    path = tmp_path / "removed_after_start" / "audit.db"
    record_result(path, make_result())  # no init_db first
    assert verify_chain(path)["valid"] and len(list_events(path)) == 1


def test_folder_that_cannot_be_created_is_a_storage_error(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the database folder should be")
    with pytest.raises(AuditStorageError):
        init_db(blocker / "audit.db")
    assert blocker.is_file()  # the file in the way is left untouched


# 2 + 3. Result insertion and retrieval
def test_record_and_get_result(db):
    event = record_result(db, make_result(), request_id="req-123")
    assert event["seq"] == 1 and event["event_type"] == "result"
    assert event["case_id"] == "CASE-0050" and event["result_id"] == "dx-0001"
    assert event["request_id"] == "req-123"
    assert event["ts"].endswith("Z") and len(event["event_id"]) == 32
    assert get_result(db, "dx-0001") == make_result()
    assert get_result(db, "dx-unknown") is None


def test_list_results_summary(db):
    record_result(db, make_result("dx-1", "CASE-0001"))
    record_result(db, make_result("dx-2", "CASE-0002"))
    record_feedback(db, "dx-1", "confirm")
    all_rows = list_results(db)
    assert [r["result_id"] for r in all_rows] == ["dx-2", "dx-1"]  # newest first
    assert all_rows[1]["feedback_count"] == 1 and all_rows[1]["outcome"] == "RECOMMEND"
    assert [r["result_id"] for r in list_results(db, case_id="CASE-0002")] == ["dx-2"]


# 4. Complete JSON preservation after sanitization
def test_every_result_field_survives(db):
    original = make_result()
    record_result(db, original)
    stored = get_result(db, "dx-0001")
    assert set(stored) == set(original)
    assert stored == original  # nothing in it is secret, so nothing changes


def test_pydantic_model_is_accepted_and_preserved(db):
    class Result(BaseModel):  # stand-in only; the real model belongs to the pipeline
        result_id: str
        case_id: str
        confidence: float
        causes: list[dict]

    record_result(db, Result(result_id="dx-pyd", case_id="CASE-0002",
                                   confidence=0.5, causes=[{"cause": "answer-a"}]))
    assert get_result(db, "dx-pyd") == {"result_id": "dx-pyd", "case_id": "CASE-0002",
                                           "confidence": 0.5, "causes": [{"cause": "answer-a"}]}


def test_same_result_cannot_be_recorded_twice(db):
    record_result(db, make_result())
    with pytest.raises(AuditValidationError):
        record_result(db, make_result(outcome="CLARIFY"))
    assert get_result(db, "dx-0001")["outcome"] == "RECOMMEND"
    assert len(list_events(db)) == 1


# 5 + 6. Feedback insertion, multiple feedback events kept
def test_feedback_is_appended_not_overwritten(db):
    record_result(db, make_result())
    first = record_feedback(db, "dx-0001", "reject", item_id="answer-a",
                            note="Latency was normal.")
    second = record_feedback(db, "dx-0001", "annotate", note="Re-checked: p95 did spike.")
    third = record_feedback(db, "dx-0001", "confirm", item_id="answer-a")

    history = get_feedback(db, "dx-0001")
    assert [f["verdict"] for f in history] == ["reject", "annotate", "confirm"]
    assert [f["feedback_id"] for f in history] == [first["feedback_id"], second["feedback_id"],
                                                    third["feedback_id"]]
    assert len({f["feedback_id"] for f in history}) == 3
    assert all(f["case_id"] == "CASE-0050" and f["created_at"].endswith("Z") for f in history)
    assert history[0]["note"] == "Latency was normal." and history[2]["note"] is None


# 7. Invalid feedback rejected (and nothing written)
@pytest.mark.parametrize("kwargs", [
    {"verdict": "approve"},
    {"verdict": "CONFIRM"},
    {"verdict": "annotate"},  # annotate needs a note
    {"verdict": "annotate", "note": "   "},
    {"verdict": "confirm", "note": 42},
    {"verdict": "confirm", "note": "x" * (audit.MAX_NOTE_CHARS + 1)},
    {"verdict": "confirm", "item_id": ""},
    {"verdict": "confirm", "item_id": "bad id with spaces"},
])
def test_invalid_feedback_is_rejected(db, kwargs):
    record_result(db, make_result())
    with pytest.raises(AuditValidationError):
        record_feedback(db, "dx-0001", **kwargs)
    assert get_feedback(db, "dx-0001") == []
    assert verify_chain(db)["valid"]


# 8. Missing result handling
def test_feedback_for_missing_result(db):
    with pytest.raises(AuditNotFoundError):
        record_feedback(db, "dx-missing", "confirm")
    with pytest.raises(AuditNotFoundError):
        get_feedback(db, "dx-missing")
    assert list_events(db) == []


# 9. Chronological ordering
def test_events_are_listed_in_chain_order(db):
    record_result(db, make_result("dx-a", "CASE-0001"))
    record_result(db, make_result("dx-b", "CASE-0002"))
    record_feedback(db, "dx-a", "confirm")
    record_feedback(db, "dx-b", "reject")
    events = list_events(db)
    assert [e["seq"] for e in events] == [1, 2, 3, 4]
    assert [(e["event_type"], e["result_id"]) for e in events] == [
        ("result", "dx-a"), ("result", "dx-b"), ("feedback", "dx-a"), ("feedback", "dx-b")]
    assert [e["ts"] for e in events] == sorted(e["ts"] for e in events)
    assert [e["seq"] for e in list_events(db, case_id="CASE-0001")] == [1, 3]
    assert [e["seq"] for e in list_events(db, event_type="feedback")] == [3, 4]
    assert [e["seq"] for e in list_events(db, limit=2, offset=1)] == [2, 3]


# 10. Persistence after reopening
def test_data_survives_reopen(tmp_path):
    path = tmp_path / "persist.db"
    record_result(path, make_result())
    record_feedback(path, "dx-0001", "confirm")
    head = verify_chain(path)["head_hash"]

    # Fresh connections only: nothing is cached in the module between calls.
    init_db(path)
    assert get_result(path, "dx-0001") == make_result()
    assert [f["verdict"] for f in get_feedback(path, "dx-0001")] == ["confirm"]
    record_feedback(path, "dx-0001", "annotate", note="after restart")
    result = verify_chain(path)
    assert result["valid"] and result["events_checked"] == 3
    assert list_events(path)[2]["prev_hash"] == head


# 11. Hash-chain linking
def test_each_event_links_to_the_previous_hash(db):
    record_result(db, make_result())
    record_feedback(db, "dx-0001", "confirm")
    record_feedback(db, "dx-0001", "annotate", note="ok")
    events = list_events(db)
    assert events[0]["prev_hash"] == GENESIS_HASH
    for before, after in zip(events, events[1:]):
        assert after["prev_hash"] == before["hash"]
    for e in events:
        assert len(e["hash"]) == 64 and int(e["hash"], 16) >= 0
        with closing(sqlite3.connect(str(db))) as conn:
            data_json = conn.execute("SELECT data_json FROM audit_events WHERE seq=?", (e["seq"],)).fetchone()[0]
        assert e["hash"] == audit.compute_hash(e["seq"], e["event_id"], e["ts"], e["event_type"],
                                               e["case_id"], e["result_id"], e["request_id"],
                                               data_json, e["prev_hash"])


# 12. Valid chain verification
def test_valid_chain_verifies(db):
    record_result(db, make_result())
    record_feedback(db, "dx-0001", "confirm")
    result = verify_chain(db)
    assert result["valid"] and result["events_checked"] == 2 and result["reason"] is None
    assert result["head_hash"] == list_events(db)[-1]["hash"]
    assert verify_chain(db, expected_head=result["head_hash"])["valid"]


# 13. Historical tampering detection
@pytest.fixture
def chain_of_four(db):
    record_result(db, make_result("dx-1", "CASE-0001"))
    record_feedback(db, "dx-1", "reject", note="first look")
    record_result(db, make_result("dx-2", "CASE-0002"))
    record_feedback(db, "dx-2", "confirm")
    head = verify_chain(db)["head_hash"]
    # An attacker with file access can drop the guard triggers; the chain must still catch edits.
    raw_execute(db, "DROP TRIGGER audit_events_no_update", "DROP TRIGGER audit_events_no_delete")
    return db, head


def test_normal_code_cannot_update_or_delete(db):
    record_result(db, make_result())
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw_execute(db, "UPDATE audit_events SET case_id = 'CASE-9999'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        raw_execute(db, "DELETE FROM audit_events")


def test_edited_data_is_detected(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "UPDATE audit_events SET data_json = replace(data_json, 'RECOMMEND', 'ESCALATE') WHERE seq = 1")
    result = verify_chain(db)
    assert not result["valid"] and result["first_bad_seq"] == 1
    assert "hash" in result["reason"]


def test_edited_feedback_verdict_is_detected(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "UPDATE audit_events SET data_json = replace(data_json, '\"reject\"', '\"confirm\"') WHERE seq = 2")
    result = verify_chain(db)
    assert not result["valid"] and result["first_bad_seq"] == 2


def test_edited_column_is_detected(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "UPDATE audit_events SET case_id = 'CASE-0099' WHERE seq = 3")
    assert verify_chain(db)["first_bad_seq"] == 3


def test_recomputed_single_hash_breaks_the_next_link(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "UPDATE audit_events SET hash = '" + "f" * 64 + "' WHERE seq = 2")
    result = verify_chain(db)
    assert not result["valid"] and result["first_bad_seq"] == 2


def test_deleted_middle_event_is_detected(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "DELETE FROM audit_events WHERE seq = 2")
    result = verify_chain(db)
    assert not result["valid"] and result["first_bad_seq"] == 2 and "missing" in result["reason"]


def test_deleted_last_event_needs_expected_head(chain_of_four):
    db, head = chain_of_four
    raw_execute(db, "DELETE FROM audit_events WHERE seq = 4")
    assert verify_chain(db)["valid"]  # documented limitation: a shortened chain still links
    result = verify_chain(db, expected_head=head)
    assert not result["valid"] and "expected_head" in result["reason"]


def test_non_canonical_data_is_detected(chain_of_four):
    db, _ = chain_of_four
    raw_execute(db, "UPDATE audit_events SET data_json = replace(data_json, ',', ', ') WHERE seq = 4")
    result = verify_chain(db)
    assert not result["valid"] and result["first_bad_seq"] == 4


# 14. Transaction rollback
def test_failed_insert_rolls_back_and_chain_continues(db):
    record_result(db, make_result("dx-ok-1"))
    # A trigger that fails INSIDE the insert transaction, after the chain head was read.
    raw_execute(db, "CREATE TRIGGER boom BEFORE INSERT ON audit_events WHEN NEW.result_id = 'dx-boom' "
                    "BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
    with pytest.raises(AuditStorageError):
        record_result(db, make_result("dx-boom"))
    assert get_result(db, "dx-boom") is None
    assert len(list_events(db)) == 1

    record_result(db, make_result("dx-ok-2"))
    events = list_events(db)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["prev_hash"] == events[0]["hash"]
    assert verify_chain(db)["valid"]


def test_corrupt_database_file_raises_storage_error(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(AuditStorageError):
        init_db(path)


# 15. Secret masking
def test_secrets_never_reach_db_events_or_csv(db):
    result = make_result(
        report={"summary": f"Client {FAKE_EMAIL} at {FAKE_IP} sent Bearer {FAKE_JWT} with key {FAKE_KEY}."},
        evidence=[{"id": "request:c-1", "kind": "request", "text": f"key {FAKE_KEY} from {FAKE_IP}",
                   "data": {"request_headers": {"Authorization": f"Bearer {FAKE_JWT}", "X-Api-Key": FAKE_KEY},
                            "token_age_s": 4000, "password": "hunter2-fake"}}],
    )
    record_result(db, result, request_id="req-1")
    record_feedback(db, "dx-0001", "annotate", note=f"Customer {FAKE_EMAIL} reported it from {FAKE_IP}.")

    stored = get_result(db, "dx-0001")
    headers = stored["evidence"][0]["data"]["request_headers"]
    assert headers == {"Authorization": "<redacted>", "X-Api-Key": "<redacted>"}
    assert stored["evidence"][0]["data"]["password"] == "<redacted>"
    assert stored["evidence"][0]["data"]["token_age_s"] == 4000  # useful numbers are kept
    # Masked with redact.mask_text: key type kept, secret part hidden; same markers as redact.py.
    summary = stored["report"]["summary"]
    assert summary == "Client <EMAIL> at 203.0.113.x sent Bearer <TOKEN> with key demo_live_<KEY>."

    outputs = {
        "database file": raw_db_text(db),
        "result": canonical_json(stored),
        "feedback": canonical_json(get_feedback(db, "dx-0001")),
        "events": canonical_json(list_events(db)),
        "csv": export_csv(db),
    }
    for name, text in outputs.items():
        assert not any(value in text for value in FAKE_VALUES), f"a fake secret reached the {name}"
        assert find_leaks(text) == [], f"{name} leaks: {find_leaks(text)}"
    assert verify_chain(db)["valid"]


def test_masking_happens_before_hashing(db):
    event = record_result(db, make_result(report={"summary": f"token {FAKE_JWT}"}))
    assert FAKE_JWT not in canonical_json(event["data"])
    assert event["hash"] == list_events(db)[0]["hash"]


def test_csv_export_shape_and_formula_guard(db):
    record_result(db, make_result())
    record_feedback(db, "dx-0001", "annotate", note="=HYPERLINK(\"http://example.invalid\")")
    lines = export_csv(db).splitlines()
    assert lines[0] == "seq,event_id,ts,event_type,case_id,result_id,request_id,data_json,prev_hash,hash"
    assert len(lines) == 3
    assert "=HYPERLINK" in lines[2]  # inside the JSON cell, which starts with "{" (not a formula)
    assert audit._csv_cell("=1+1") == "'=1+1" and audit._csv_cell("{}") == "{}"


# 16. Invalid input handling
@pytest.mark.parametrize("bad", [
    None, "not a dict", ["list"],
    {"case_id": "CASE-0001"},                                   # no result_id
    {"result_id": "dx-1"},                                  # no case_id
    {"result_id": "", "case_id": "CASE-0001"},
    {"result_id": "dx 1", "case_id": "CASE-0001"},
    {"result_id": "dx-1", "case_id": "=cmd|' /C calc'!A0"},
    {"result_id": "dx-1", "case_id": "CASE-0001", "confidence": float("nan")},
    {"result_id": "dx-1", "case_id": "CASE-0001", "obj": object()},
    {"result_id": "dx-1", "case_id": "CASE-0001", "bad_keys": {1: "x"}},
])
def test_invalid_result_is_rejected(db, bad):
    with pytest.raises(AuditValidationError):
        record_result(db, bad)
    assert list_events(db) == []


@pytest.mark.parametrize("call", [
    lambda db: record_result(db, make_result(), request_id="bad request id"),
    lambda db: list_events(db, limit=0),
    lambda db: list_events(db, limit=audit.MAX_LIMIT + 1),
    lambda db: list_events(db, offset=-1),
    lambda db: list_events(db, limit=True),
    lambda db: list_events(db, event_type="delete_everything"),
    lambda db: list_results(db, case_id="CASE-0001' OR '1'='1"),
    lambda db: get_result(db, None),
    lambda db: init_db(""),
])
def test_invalid_arguments_are_rejected(db, call):
    with pytest.raises(AuditValidationError):
        call(db)


# Deterministic JSON serialisation
def test_canonical_json_is_deterministic():
    a = {"b": 1, "a": {"y": [1, 2], "x": "é"}}
    b = {"a": {"x": "é", "y": [1, 2]}, "b": 1}
    assert canonical_json(a) == canonical_json(b) == '{"a":{"x":"é","y":[1,2]},"b":1}'
    with pytest.raises(AuditValidationError):
        canonical_json({"x": float("inf")})


def test_sanitize_converts_types_consistently():
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    out = sanitize({"when": datetime(2026, 10, 15, 10, 47, 22, 435000, tzinfo=ist),
                    "tags": ("a", "b"), "set": {"z", "y"}})
    assert out == {"when": "2026-10-15T05:17:22.435Z", "tags": ["a", "b"], "set": ["y", "z"]}


def test_same_content_gives_same_hash_in_different_key_order(tmp_path):
    forward = make_result()
    backward = dict(reversed(list(make_result().items())))
    e1 = record_result(tmp_path / "a.db", forward)
    e2 = record_result(tmp_path / "b.db", backward)
    assert canonical_json(e1["data"]) == canonical_json(e2["data"])
    same_fields = (1, e1["event_id"], e1["ts"], "result", "CASE-0050", "dx-0001", None)
    assert audit.compute_hash(*same_fields, canonical_json(e1["data"]), GENESIS_HASH) == \
        audit.compute_hash(*same_fields, canonical_json(e2["data"]), GENESIS_HASH)
