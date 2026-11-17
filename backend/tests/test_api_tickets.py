"""Ticket routes (app/api/tickets.py) through FastAPI's TestClient. Search runs on a tiny in-memory index;
no model download, no database (db/repo.py is not merged yet, so those routes answer 503)."""
import pytest
from fastapi.testclient import TestClient

from app.api import tickets as api
from app.auth.deps import current_user
from app.main import create_app
from app.tickets import pipeline
from app.tickets.models import User
from app.tickets.similar import build_ticket_index

EMAIL = "anna.k@example.com"


def row(tid, category, subcategory, service, created, subject):
    return {"ticket_id": tid, "created_at": created, "resolved_at": "", "status": "Resolved", "channel": "portal",
            "category": category, "subcategory": subcategory, "priority": "P3", "assigned_team": "Service Desk",
            "affected_service": service, "environment": "laptop", "subject": subject,
            "description": f"{subject}. Contact {EMAIL} for details.", "root_cause": "Old profile",
            "resolution_notes": "Reset the profile.", "resolution_steps": "Reset profile | Reconnect",
            "problem_id": "", "dup_group_id": tid, "split": "train"}


ROWS = [row("INC1", "Network", "VPN", "VPN", "2026-01-10T09:00:00", "VPN error 809 from home"),
        row("INC2", "Hardware", "Printer", "Printing", "2026-03-01T09:00:00", "Printer paper jam"),
        row("INC3", "Network", "Wi-Fi", "Wi-Fi", "2026-02-01T09:00:00", "Wi-Fi drops in meeting room")]
TICKET = {"subject": "VPN won't connect, error 809",
          "description": f"VPN fails with error 809 from home. Reply to {EMAIL}.", "affected_service": "VPN"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    index = build_ticket_index(ROWS)  # BM25 only (conftest), masked
    monkeypatch.setattr(api, "get_ticket_index", lambda: index)
    monkeypatch.setattr(pipeline, "search_similar", lambda *a, **k: [])  # no index, no model in analyze
    app = create_app(audit_db_path=tmp_path / "audit.db", warmups=[])
    # These tests are about the routes, not login (tests/test_auth.py covers that): sign in as a test lead,
    # who may use every route.
    app.dependency_overrides[current_user] = lambda: User(username="demo", role="lead")
    return TestClient(app)


def test_an_employee_cannot_use_any_analyst_route(client):
    client.app.dependency_overrides[current_user] = lambda: User(username="emma", role="employee")
    calls = [("post", "/api/tickets/analyze", TICKET), ("get", "/api/tickets", None),
             ("get", "/api/tickets/INC1", None), ("get", "/api/tickets/presets", None),
             ("get", "/api/tickets/taxonomy", None), ("get", "/api/tickets/history", None),
             ("get", "/api/tickets/stats", None),
             ("post", "/api/tickets/results/abc123/feedback", {"verdict": "useful"}),
             ("post", "/api/tickets/results/abc123/resolve", None)]
    for method, path, body in calls:
        r = client.request(method.upper(), path, json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN", path
    client.app.dependency_overrides[current_user] = lambda: User(username="ana", role="analyst")
    assert client.post("/api/tickets/analyze", json=TICKET).status_code == 200
    assert client.get("/api/tickets/stats").status_code == 403  # lead only


def test_analyze_returns_the_envelope_with_an_outcome(client):
    r = client.post("/api/tickets/analyze", json=TICKET)
    body = r.json()
    assert r.status_code == 200 and body["ok"] is True and body["error"] is None
    assert body["data"]["outcome"] in {"RECOMMEND", "CLARIFY", "ESCALATE", "BLOCKED"}
    assert len(body["data"]["result_id"]) == 32


def test_an_email_in_the_request_never_comes_back(client):
    r = client.post("/api/tickets/analyze", json=TICKET)
    assert r.status_code == 200 and EMAIL not in r.text and "<EMAIL>" in r.text
    bad = client.post("/api/tickets/analyze", json={**TICKET, "subject": ""})  # error path: no echo either
    assert bad.status_code == 400 and EMAIL not in bad.text


def test_empty_subject_is_a_400(client):
    r = client.post("/api/tickets/analyze", json={**TICKET, "subject": ""})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "VALIDATION_ERROR" and "subject" in r.json()["error"]["message"]


def test_analyze_failure_is_a_clean_500(client, monkeypatch):
    def boom(ticket, user):
        raise RuntimeError(f"mask failed on {EMAIL}")

    monkeypatch.setattr(api, "analyze", boom)
    r = client.post("/api/tickets/analyze", json=TICKET)
    assert r.status_code == 500 and r.json()["error"]["code"] == "ANALYSIS_FAILED"
    assert EMAIL not in r.text and "Traceback" not in r.text


def test_presets_lists_five(client):
    data = client.get("/api/tickets/presets").json()["data"]
    assert len(data) == 5 and set(data[0]) == {"id", "label", "expected_outcome", "ticket"}


def test_search_applies_filters_and_sorts_newest_first(client):
    data = client.get("/api/tickets", params={"category": "Network"}).json()["data"]
    assert [d["ticket_id"] for d in data] == ["INC3", "INC1"]
    assert set(data[0]) == set(api.LIST_FIELDS)  # no description: list fields only
    hits = client.get("/api/tickets", params={"q": "VPN error 809", "category": "Network"}).json()["data"]
    assert hits[0]["ticket_id"] == "INC1" and "score" in hits[0]
    assert all(h["ticket_id"] != "INC2" for h in hits)


def test_taxonomy_lists_distinct_values(client):
    data = client.get("/api/tickets/taxonomy").json()["data"]
    assert data["category"] == ["Hardware", "Network"]
    assert data["subcategory"] == {"Hardware": ["Printer"], "Network": ["VPN", "Wi-Fi"]}


def test_one_ticket_is_masked_and_unknown_is_404(client):
    r = client.get("/api/tickets/INC1")
    data = r.json()["data"]
    assert r.status_code == 200 and data["resolution_text"] == "Reset the profile. Steps: Reset profile | Reconnect"
    assert EMAIL not in r.text and "<EMAIL>" in data["description"]
    missing = client.get("/api/tickets/INC999")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "TICKET_NOT_FOUND"


def test_feedback_without_the_repository_is_503(client):
    r = client.post("/api/tickets/results/abc123/feedback", json={"verdict": "useful"})
    assert r.status_code == 503
    assert r.json()["error"] == {"code": "DB_NOT_READY", "message": "Saving is not available yet"}
    for path in ("/api/tickets/history", "/api/tickets/stats"):
        assert client.get(path).json()["error"]["code"] == "DB_NOT_READY"
    assert client.post("/api/tickets/results/abc123/resolve").status_code == 503


def test_bad_result_id_is_400(client):
    r = client.post("/api/tickets/results/bad!id/feedback", json={"verdict": "useful"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "BAD_ID"
    assert client.get("/api/tickets/bad!id").status_code == 400


def test_repository_is_used_when_present(client, monkeypatch):
    saved = []

    def fake_optional(module, name):
        if module != "app.db.repo":
            return None
        return {"add_feedback": lambda rid, user, fb: saved.append((rid, user.username, fb.verdict)) or {"id": 1},
                "resolve": lambda rid, user: "2026-11-17T12:00:00+00:00"}.get(name)

    monkeypatch.setattr(api, "optional", fake_optional)
    r = client.post("/api/tickets/results/abc123/feedback", json={"verdict": "incorrect", "note": "wrong fix"})
    assert r.status_code == 201 and saved == [("abc123", "demo", "incorrect")]
    bad = client.post("/api/tickets/results/abc123/feedback", json={"verdict": "confirm"})
    assert bad.status_code == 400  # verdicts are useful / incorrect / unresolved
    assert client.post("/api/tickets/results/abc123/resolve").json()["data"]["resolved_at"].startswith("2026")
