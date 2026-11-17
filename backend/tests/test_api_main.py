"""FastAPI app (app/main.py): envelope, request ids, CORS, health, readiness after warm-up, audit routes."""
import threading

import pytest
from fastapi.testclient import TestClient

from app.auth.deps import current_user
from app.common import audit
from app.main import create_app
from app.tickets.models import User


def make_client(tmp_path, warmups=()):
    return TestClient(create_app(audit_db_path=tmp_path / "audit" / "test.db", warmups=list(warmups)))


def wait_for_warmup(client) -> None:
    for _ in range(200):
        if client.app.state.warmup.state != "warming":
            return
        threading.Event().wait(0.01)
    raise AssertionError("warm-up did not finish")


def test_health_is_ok_and_uses_the_envelope(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200 and body["ok"] is True and body["error"] is None
    assert body["data"]["api"] == "up" and body["data"]["database"] == "up"
    assert response.headers["X-Request-ID"]


def test_ready_only_after_warmup(tmp_path) -> None:
    release = threading.Event()
    with make_client(tmp_path, warmups=[lambda: release.wait(5)]) as client:
        early = client.get("/health/ready")
        assert early.status_code == 503 and early.json()["error"]["code"] == "WARMING_UP"
        assert client.get("/health").json()["data"]["warmup"] == "warming"
        release.set()
        wait_for_warmup(client)
        ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["data"]["warmup"] == "ready"


def test_failed_warmup_is_reported(tmp_path) -> None:
    def broken():
        raise RuntimeError("model missing")

    with make_client(tmp_path, warmups=[broken]) as client:
        wait_for_warmup(client)
        response = client.get("/health/ready")
        health = client.get("/health").json()["data"]
    assert response.status_code == 503 and response.json()["error"]["code"] == "WARMUP_FAILED"
    assert health["warmup"] == "failed" and "RuntimeError" in health["warmup_detail"]


def test_request_id_is_reused_when_plain_and_replaced_when_not(tmp_path) -> None:
    with make_client(tmp_path) as client:
        same = client.get("/health", headers={"X-Request-ID": "abc-123"})
        replaced = client.get("/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert same.headers["X-Request-ID"] == "abc-123"
    assert replaced.headers["X-Request-ID"] != "bad id\nwith newline"


def test_unknown_route_uses_the_error_envelope(tmp_path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/nope")
    assert response.status_code == 404
    assert response.json() == {"ok": False, "data": None, "error": {"code": "NOT_FOUND", "message": "Not Found"}}


def test_cors_from_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://127.0.0.1:5173")
    with make_client(tmp_path) as client:
        allowed = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
        other = client.get("/health", headers={"Origin": "http://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in other.headers


def test_cors_preflight_allows_the_login_token(tmp_path, monkeypatch) -> None:
    # The deployed frontend (Vercel) calls the API (Render) cross-site with Authorization: Bearer <JWT>.
    monkeypatch.setenv("CORS_ORIGINS", "https://visionquest.vercel.app")
    with make_client(tmp_path) as client:
        preflight = client.options("/api/tickets/analyze", headers={
            "Origin": "https://visionquest.vercel.app", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization, content-type"})
    assert preflight.status_code == 200
    assert "authorization" in preflight.headers["access-control-allow-headers"].lower()


def test_wildcard_cors_is_refused(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(ValueError):
        create_app(audit_db_path=tmp_path / "a.db")


def test_unexpected_errors_hide_details(tmp_path) -> None:
    app = create_app(audit_db_path=tmp_path / "a.db", warmups=[])

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500 and "secret" not in response.text
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"


def test_audit_routes_verify_read_and_add_feedback(tmp_path) -> None:
    db = tmp_path / "audit" / "test.db"
    audit.record_result(db, {"result_id": "r-1", "case_id": "CASE-1", "outcome": "RECOMMEND", "summary": "s"})
    with make_client(tmp_path) as client:
        # The audit routes need an analyst (tests/test_auth.py checks the 401/403); sign in as a test analyst here.
        client.app.dependency_overrides[current_user] = lambda: User(username="test", role="analyst")
        assert client.get("/api/audit/verify").json()["data"]["valid"] is True
        listed = client.get("/api/audit/results").json()["data"]
        assert [r["result_id"] for r in listed] == ["r-1"] and listed[0]["summary"] == "s"
        assert client.get("/api/audit/results/r-1").json()["data"]["case_id"] == "CASE-1"
        posted = client.post("/api/audit/results/r-1/feedback", json={"verdict": "annotate", "note": "looks right"},
                             headers={"X-Request-ID": "req-7"})
        assert posted.status_code == 201 and posted.json()["data"]["request_id"] == "req-7"
        assert [f["verdict"] for f in client.get("/api/audit/results/r-1/feedback").json()["data"]] == ["annotate"]
        missing = client.get("/api/audit/results/r-404")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "RESULT_NOT_FOUND"
        bad = client.post("/api/audit/results/r-1/feedback", json={"verdict": "annotate"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"
        assert client.get("/api/audit/verify").json()["data"]["events_checked"] == 2


def test_audit_folder_is_created_on_startup(tmp_path) -> None:
    with make_client(tmp_path) as client:
        client.get("/health")
    assert (tmp_path / "audit" / "test.db").is_file()


def test_health_checks_the_real_database_and_answers_under_api_too(tmp_path) -> None:
    with make_client(tmp_path) as client:
        plain = client.get("/health").json()["data"]
        api = client.get("/api/health").json()["data"]
        ready = client.get("/api/health/ready")
    assert plain["database"] == api["database"] == "up"
    assert plain["database_detail"] == "sqlite: SELECT 1 ok"    # conftest's SQLite file, not the audit store
    assert ready.status_code == 200 and ready.json()["ok"] is True


def test_database_down_is_reported_not_a_500(tmp_path, monkeypatch) -> None:
    from app import main as main_mod

    class OperationalError(Exception):
        pass

    def broken_ping():
        raise OperationalError("could not connect to postgresql://user:secret@host/db")

    monkeypatch.setattr(main_mod, "optional", lambda module, name: broken_ping if name == "ping" else None)
    with make_client(tmp_path, warmups=[]) as client:
        wait_for_warmup(client)
        health = client.get("/api/health")
        ready = client.get("/health/ready")
    assert health.status_code == 200
    assert health.json()["data"]["database"] == "down"
    assert health.json()["data"]["database_detail"] == "OperationalError"
    assert "secret" not in health.text
    assert ready.status_code == 503 and ready.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_without_app_db_health_falls_back_to_the_legacy_audit_store(tmp_path, monkeypatch) -> None:
    from app import main as main_mod
    monkeypatch.setattr(main_mod, "optional", lambda module, name: None)
    with make_client(tmp_path) as client:
        data = client.get("/health").json()["data"]
    assert data["database"] == "up" and "legacy audit store" in data["database_detail"]
