"""db/repo.py on a fresh SQLite file per test: results, append-only feedback, idempotent resolve, stats,
feedback_counts, the audit chain (and tamper detection), demo-user seeding + login, URL rewriting, API 404."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.auth.users import find_user
from app.db import engine as db_engine
from app.db import repo
from app.db.tables import AnalysisResultRow, Base, FeedbackRow, init_db
from app.tickets.models import (AnalysisResult, Classification, FeedbackIn, LabelScore, SimilarTicket, TicketIn,
                                User)

ANA = User(username="ana", role="analyst")
LEE = User(username="lee", role="lead")
T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'app.db').as_posix()}")
    monkeypatch.setenv("PASSWORD_HASH_ITERATIONS", "1000")
    monkeypatch.delenv("DEMO_USERS", raising=False)
    db_engine.reset_engine()
    Base.metadata.create_all(db_engine.get_engine())
    yield
    db_engine.reset_engine()   # the next test file gets conftest's database back


def result(rid="r1", category="Network", outcome="RECOMMEND", subject="VPN error 809 for <EMAIL>",
           created=T0, similar_ids=("INC1",)):
    cls = Classification(category=LabelScore(label=category, prob=0.8), subcategory=LabelScore(label="VPN", prob=0.7),
                         priority=LabelScore(label="P3", prob=1.0), priority_rule="IMPACT_NOT_GIVEN",
                         model_version="test")
    similar = [SimilarTicket(ticket_id=t, subject="s", category=category, status="Resolved", score=0.9,
                             match="related") for t in similar_ids]
    return AnalysisResult(result_id=rid, created_at=created, recommended_at=created, outcome=outcome,
                          ticket=TicketIn(subject=subject, description="VPN fails from home."),
                          classification=cls, similar=similar, confidence=0.8, reasons=["STRONG_MATCH"])


def test_save_then_history_shows_it_newest_first():
    repo.save_result(result("old", created=T0), ANA)
    repo.save_result(result("new", created=T0 + timedelta(hours=1)), ANA)
    rows = repo.history(ANA, limit=10, offset=0)
    assert [r["result_id"] for r in rows] == ["new", "old"]
    row = rows[0]
    assert {k: row[k] for k in ("result_id", "created_at", "subject", "outcome", "confidence", "latest_verdict",
                                "resolved_at", "reviewer")} == {
        "result_id": "new", "created_at": (T0 + timedelta(hours=1)).isoformat(),
        "subject": "VPN error 809 for <EMAIL>", "outcome": "RECOMMEND", "confidence": 0.8,
        "latest_verdict": None, "resolved_at": None, "reviewer": None}
    assert [r["result_id"] for r in repo.history(ANA, limit=1, offset=1)] == ["old"]


def test_history_rows_have_the_shape_the_frontend_adapter_reads():
    """frontend/src/lib/api.ts toHistoryEntry() needs these keys (Review History and Audit pages)."""
    repo.save_result(result(), ANA)
    repo.add_feedback("r1", ANA, FeedbackIn(verdict="incorrect", note="no"))
    repo.add_feedback("r1", LEE, FeedbackIn(verdict="useful", target="match", target_id="INC1"))
    repo.resolve("r1", LEE)
    row = repo.history(ANA)[0]
    assert row["ticket_masked"]["subject"] == "VPN error 809 for <EMAIL>"
    assert row["user_id"] == "ana" and row["evidence_ids"] == ["INC1"] and row["reasons"] == ["STRONG_MATCH"]
    assert row["classification"]["category"]["label"] == "Network" and row["model_version"] == "test"
    assert [f["verdict"] for f in row["feedback"]] == ["incorrect", "useful"]          # oldest first, all targets
    assert row["feedback"][1] | {"created_at": None} == {"verdict": "useful", "note_masked": None,
                                                         "reviewer_id": "lee", "target": "match",
                                                         "target_id": "INC1", "created_at": None}
    assert row["resolution"]["resolved_by"] == "lee" and row["resolution"]["resolved_at"] == row["resolved_at"]
    assert row["latest_verdict"] == "incorrect" and row["reviewer"] == "ana"          # match verdicts don't count


def test_ping_answers_the_dialect():
    assert repo.ping() == "sqlite"


def test_an_employee_sees_only_their_own_results():
    repo.save_result(result("mine"), User(username="emma", role="employee"))
    repo.save_result(result("other"), ANA)
    assert [r["result_id"] for r in repo.history(User(username="emma", role="employee"))] == ["mine"]
    assert len(repo.history(ANA)) == 2


def test_unmasked_ticket_text_is_refused():
    with pytest.raises(repo.NotMaskedError):
        repo.save_result(result(subject="VPN broken for anna.k@example.com"), ANA)
    assert repo.history(ANA) == []


def test_feedback_is_append_only_and_the_latest_verdict_wins():
    repo.save_result(result(), ANA)
    first = repo.add_feedback("r1", ANA, FeedbackIn(verdict="incorrect", note="wrong, ask anna.k@example.com"))
    repo.add_feedback("r1", LEE, FeedbackIn(verdict="useful"))
    assert first["verdict"] == "incorrect"
    row = repo.history(ANA)[0]
    assert row["latest_verdict"] == "useful" and row["reviewer"] == "lee"
    with db_engine.get_session() as s:
        notes = [f.note_masked for f in s.query(FeedbackRow).order_by(FeedbackRow.id)]
    assert len(notes) == 2 and "anna.k@example.com" not in notes[0] and "<EMAIL>" in notes[0]


def test_resolve_is_idempotent():
    repo.save_result(result(), ANA)
    first = repo.resolve("r1", ANA)
    assert repo.resolve("r1", LEE) == first
    assert repo.history(ANA)[0]["resolved_at"] == first.isoformat()


def test_unknown_result_is_lookup_error():
    with pytest.raises(LookupError):
        repo.add_feedback("nope", ANA, FeedbackIn(verdict="useful"))
    with pytest.raises(LookupError):
        repo.resolve("nope", ANA)


def test_stats_counts_outcomes_useful_rate_and_mttr(monkeypatch):
    now = datetime.now(timezone.utc)
    repo.save_result(result("a", category="Network", created=now - timedelta(minutes=30)), ANA)
    repo.save_result(result("b", category="Network", created=now - timedelta(minutes=10)), ANA)
    repo.save_result(result("c", category="Endpoint", outcome="ESCALATE", created=now), ANA)
    repo.add_feedback("a", ANA, FeedbackIn(verdict="incorrect"))
    repo.add_feedback("a", ANA, FeedbackIn(verdict="useful"))          # changed mind: latest counts
    repo.add_feedback("b", ANA, FeedbackIn(verdict="incorrect"))
    repo.add_feedback("c", ANA, FeedbackIn(verdict="useful", target="match", target_id="INC1"))  # not a result verdict
    repo.resolve("a", ANA)
    repo.resolve("b", ANA)
    st = repo.stats()
    assert st["n_results"] == 3 and st["n_feedback"] == 4
    assert st["outcome_counts"] == {"RECOMMEND": 2, "ESCALATE": 1}
    assert st["useful_rate_by_category"] == {
        "Network": {"useful": 1, "incorrect": 1, "unresolved": 0, "rated": 2, "useful_rate": 0.5}}
    assert st["mttr"]["resolved_count"] == 2 and 15 <= st["mttr"]["median_minutes"] <= 25


def test_feedback_counts_for_matches():
    repo.save_result(result("r1"), ANA)
    repo.save_result(result("r2"), ANA)
    match = lambda v, t="INC1": FeedbackIn(verdict=v, target="match", target_id=t)  # noqa: E731
    repo.add_feedback("r1", ANA, match("incorrect"))
    repo.add_feedback("r1", ANA, match("useful"))      # same reviewer, same result: only the latest counts
    repo.add_feedback("r2", LEE, match("useful"))
    repo.add_feedback("r2", LEE, match("unresolved", "INC2"))
    repo.add_feedback("r2", LEE, FeedbackIn(verdict="incorrect"))   # a result verdict, not a match
    assert repo.feedback_counts(["INC1", "INC2", "INC9"]) == {
        "INC1": {"useful": 2, "incorrect": 0, "unresolved": 0},
        "INC2": {"useful": 0, "incorrect": 0, "unresolved": 1}}
    assert repo.feedback_counts([]) == {}


def test_audit_chain_verifies_and_detects_a_tampered_row():
    repo.save_result(result(), ANA)
    repo.add_feedback("r1", ANA, FeedbackIn(verdict="useful"))
    repo.resolve("r1", ANA)
    assert repo.verify_chain() == {"ok": True, "first_broken_seq": None, "n_events": 3}
    with db_engine.get_session() as s:      # someone edits the stored result behind the app's back
        s.execute(update(AnalysisResultRow).where(AnalysisResultRow.result_id == "r1").values(outcome="ESCALATE"))
    assert repo.verify_chain() == {"ok": False, "first_broken_seq": 1, "n_events": 1}


def test_init_db_seeds_demo_users_once_and_login_works(monkeypatch):
    monkeypatch.setenv("DEMO_USERS", "ana:pw-one:analyst,lee:pw-two:lead")
    assert init_db() == ["ana", "lee"]
    monkeypatch.setenv("DEMO_USERS", "ana:changed:lead,emma:pw-three:employee")
    assert init_db() == ["emma"]                       # ana is never overwritten
    row = repo.get_user("ana")
    assert row["role"] == "analyst" and "pw-one" not in row["password_hash"]
    assert repo.get_user("ghost") is None
    user, _ = find_user("ana")                         # login's lookup reads the database
    assert user == User(username="ana", role="analyst")
    assert repo.verify_chain()["ok"]


def test_login_through_the_api(monkeypatch, tmp_path):
    from app.main import create_app
    monkeypatch.setenv("DEMO_USERS", "ana:pw-one:analyst")
    monkeypatch.setenv("JWT_SECRET", "x" * 40)
    init_db()
    client = TestClient(create_app(audit_db_path=tmp_path / "audit.db", warmups=[]))
    assert client.post("/api/auth/login", json={"username": "ana", "password": "pw-one"}).status_code == 200
    assert client.post("/api/auth/login", json={"username": "ana", "password": "nope"}).status_code == 401


def test_api_unknown_result_is_404_and_lead_can_verify(tmp_path):
    from app.auth.deps import current_user
    from app.main import create_app
    app = create_app(audit_db_path=tmp_path / "audit.db", warmups=[])
    app.dependency_overrides[current_user] = lambda: LEE
    client = TestClient(app)
    r = client.post("/api/tickets/results/nope/feedback", json={"verdict": "useful"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "RESULT_NOT_FOUND"
    assert client.post("/api/tickets/results/nope/resolve").status_code == 404
    assert client.get("/api/tickets/audit/verify").json()["data"]["ok"] is True
    app.dependency_overrides[current_user] = lambda: ANA
    assert client.get("/api/tickets/audit/verify").status_code == 403


@pytest.mark.parametrize("raw, expected", [
    ("postgres://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
    ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///x.db", "sqlite:///x.db"),
])
def test_database_url_rewrite(raw, expected):
    assert db_engine.database_url(raw) == expected


def test_empty_url_is_the_local_sqlite_file():
    assert db_engine.database_url("").endswith("backend/data/app.db")
