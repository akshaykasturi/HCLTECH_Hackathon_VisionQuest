"""Login: password hashing, tokens, the user store, the routes and the role checks."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient

from app.auth import router as auth_router_module
from app.auth import users as users_module
from app.auth.deps import current_user, require_role
from app.auth.security import (ALGORITHM, ISSUER, AuthConfigError, InvalidToken, create_token, decode_token,
                               hash_password, verify_password)
from app.auth.users import DemoUsersError, find_user, parse_demo_users
from app.common.api import ok
from app.main import create_app
from app.tickets.models import User

SECRET = "test-secret-" + "x" * 40
DEMO = "ana:pw-ana-1:analyst, lee:p:w:2:lead"   # lee's password contains ':' on purpose


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DEMO_USERS", DEMO)
    monkeypatch.setenv("PASSWORD_HASH_ITERATIONS", "1000")   # fast tests; real default is 600000
    monkeypatch.delenv("JWT_EXPIRE_HOURS", raising=False)
    monkeypatch.setattr(users_module, "_db_get_user", lambda: None)  # no database in these tests
    auth_router_module.fails.clear()
    yield
    auth_router_module.fails.clear()


@pytest.fixture
def client(tmp_path):
    app = create_app(audit_db_path=tmp_path / "audit.db", warmups=[])
    # A tiny protected router, to test the dependencies the way other routes will use them.
    probe = APIRouter(prefix="/probe")

    @probe.get("/any")
    def any_user(user: Annotated[User, Depends(current_user)]):
        return ok(user.model_dump())

    @probe.get("/lead")
    def lead_only(user: Annotated[User, Depends(require_role("lead"))]):
        return ok(user.model_dump())

    app.include_router(probe)
    return TestClient(app)


def login(client, username="ana", password="pw-ana-1"):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- passwords --------------------------------------------------------------------------------

def test_password_hash_round_trip_and_never_holds_the_password():
    stored = hash_password("Summer2024!")
    assert stored.startswith("pbkdf2_sha256$1000$") and "Summer2024" not in stored
    assert verify_password("Summer2024!", stored)
    assert not verify_password("summer2024!", stored)


def test_same_password_gets_a_different_salt_each_time():
    assert hash_password("same") != hash_password("same")


@pytest.mark.parametrize("stored", ["", "plain-text", "md5$1$a$b", "pbkdf2_sha256$x$y$z"])
def test_malformed_stored_hash_is_just_false(stored):
    assert verify_password("anything", stored) is False


# ---- tokens -----------------------------------------------------------------------------------

def test_token_round_trip():
    token, expires = create_token("ana", "analyst")
    claims = decode_token(token)
    assert claims["sub"] == "ana" and claims["role"] == "analyst" and claims["iss"] == ISSUER
    assert timedelta(hours=7.9) < expires - datetime.now(timezone.utc) <= timedelta(hours=8)


def test_expired_token_is_rejected():
    token, _ = create_token("ana", "analyst", now=datetime.now(timezone.utc) - timedelta(hours=9))
    with pytest.raises(InvalidToken, match="expired"):
        decode_token(token)


def test_token_with_a_changed_role_is_rejected():
    token, _ = create_token("ana", "analyst")
    claims = jwt.decode(token, SECRET, algorithms=[ALGORITHM], issuer=ISSUER)
    forged = jwt.encode({**claims, "role": "lead"}, "some-other-secret-" + "y" * 30, algorithm=ALGORITHM)
    with pytest.raises(InvalidToken):
        decode_token(forged)


def test_unsigned_none_algorithm_token_is_rejected():
    forged = jwt.encode({"sub": "ana", "role": "lead", "iss": ISSUER, "iat": 1, "exp": 4102444800},
                        None, algorithm="none")
    with pytest.raises(InvalidToken):
        decode_token(forged)


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_missing_or_short_secret_stops_login(monkeypatch, secret):
    monkeypatch.setenv("JWT_SECRET", secret)
    with pytest.raises(AuthConfigError) as exc:
        create_token("ana", "analyst")
    assert secret == "" or secret not in str(exc.value)


# ---- user store -------------------------------------------------------------------------------

def test_parse_demo_users_allows_colons_in_passwords():
    assert parse_demo_users(DEMO) == [("ana", "pw-ana-1", "analyst"), ("lee", "p:w:2", "lead")]


@pytest.mark.parametrize("value,part", [("ana:pw", "entry 1"), ("ana:pw:boss", "role"), (":pw:analyst", "empty")])
def test_bad_demo_users_name_the_entry_not_the_password(value, part):
    with pytest.raises(DemoUsersError) as exc:
        parse_demo_users(value)
    assert part in str(exc.value) and "pw" not in str(exc.value).replace("password", "")


def test_database_user_wins_when_the_repo_exists(monkeypatch):
    stored = hash_password("db-pass")
    monkeypatch.setattr(users_module, "_db_get_user",
                        lambda: lambda name: {"username": name, "password_hash": stored, "role": "lead"}
                        if name == "dbuser" else None)
    user, h = find_user("dbuser")
    assert user == User(username="dbuser", role="lead") and verify_password("db-pass", h)
    assert find_user("ana") is None   # the database is the source once it exists


def test_database_down_falls_back_to_demo_users(monkeypatch):
    def broken(name):
        raise ConnectionError("db down")
    monkeypatch.setattr(users_module, "_db_get_user", lambda: broken)
    assert find_user("ana")[0] == User(username="ana", role="analyst")


# ---- routes -----------------------------------------------------------------------------------

def test_login_returns_a_token_that_opens_me(client):
    r = login(client)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["token_type"] == "bearer" and data["user"] == {"username": "ana", "role": "analyst"}
    me = client.get("/api/auth/me", headers=bearer(data["token"]))
    assert me.status_code == 200 and me.json()["data"] == {"username": "ana", "role": "analyst"}


def test_wrong_password_and_unknown_user_get_the_same_answer(client):
    a, b = login(client, password="nope"), login(client, username="ghost")
    assert a.status_code == b.status_code == 401
    assert a.json()["error"] == b.json()["error"] == {"code": "INVALID_LOGIN",
                                                       "message": "Wrong username or password."}


def test_too_many_wrong_tries_pause_that_username(client):
    for _ in range(auth_router_module.MAX_FAILS):
        assert login(client, password="nope").status_code == 401
    r = login(client)   # even the right password waits
    assert r.status_code == 429 and r.json()["error"]["code"] == "TOO_MANY_ATTEMPTS"
    assert login(client, username="lee", password="p:w:2").status_code == 200   # other users unaffected


def test_login_without_jwt_secret_is_503_not_a_crash(client, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "")
    r = login(client)
    assert r.status_code == 503 and r.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_password_never_appears_in_any_response(client):
    for r in (login(client), login(client, password="pw-ana-1-wrong")):
        assert "pw-ana-1" not in r.text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Bearer abc.def.ghi"},
                                     {"Authorization": "Basic YW5hOnB3"}])
def test_protected_route_needs_a_valid_token(client, headers):
    r = client.get("/probe/any", headers=headers)
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_analyst_cannot_open_a_lead_route_but_a_lead_can_open_both(client):
    analyst = login(client).json()["data"]["token"]
    lead = login(client, "lee", "p:w:2").json()["data"]["token"]
    r = client.get("/probe/lead", headers=bearer(analyst))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    assert client.get("/probe/lead", headers=bearer(lead)).status_code == 200
    assert client.get("/probe/any", headers=bearer(lead)).status_code == 200


def test_unknown_role_in_require_role_is_a_programming_error():
    with pytest.raises(ValueError):
        require_role("admin")


def test_other_route_tests_can_skip_login_with_an_override(client):
    client.app.dependency_overrides[current_user] = lambda: User(username="t", role="analyst")
    assert client.get("/probe/any").json()["data"] == {"username": "t", "role": "analyst"}


def test_ticket_routes_need_login(client):
    """The real ticket routes (app/api/tickets.py) are closed without a token and open with one."""
    assert client.post("/api/tickets/analyze", json={"subject": "VPN down", "description": "x"}).status_code == 401
    assert client.get("/api/tickets/taxonomy").status_code == 401
    token = login(client).json()["data"]["token"]
    assert client.get("/api/tickets/stats", headers=bearer(token)).status_code == 403   # analyst, not lead
