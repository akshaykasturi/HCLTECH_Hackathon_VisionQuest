"""Login routes (/api/auth). Every response uses the {ok, data, error} envelope.

  POST /api/auth/login   LoginIn -> {token, token_type, expires_at, user}      public
  GET  /api/auth/me      -> the signed-in User                                  any role

A wrong username and a wrong password get the same answer (and take the same time), so nobody can
find out which usernames exist. After MAX_FAILS wrong tries for one username within FAIL_WINDOW_S,
that username is paused (429) until the window passes. The counter lives in memory: it resets on a
restart and is per server process, which is enough for a single Render instance.

Nothing here logs a password or a token.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth.deps import current_user
from app.auth.security import AuthConfigError, create_token, dummy_verify, verify_password
from app.auth.users import find_user
from app.common.api import ApiError, ok
from app.tickets.models import LoginIn, User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILS = 5
FAIL_WINDOW_S = 300.0


class _FailCounter:
    """Recent failed logins per username (lower case), in memory."""

    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._fails[key]
        while q and now - q[0] > FAIL_WINDOW_S:
            q.popleft()
        return q

    def locked(self, username: str) -> bool:
        with self._lock:
            return len(self._recent(username.lower(), time.monotonic())) >= MAX_FAILS

    def fail(self, username: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(username.lower(), now).append(now)

    def clear(self, username: str | None = None) -> None:
        with self._lock:
            if username is None:
                self._fails.clear()
            else:
                self._fails.pop(username.lower(), None)


fails = _FailCounter()


@router.post("/login")
def login(body: LoginIn):
    username = body.username.strip()
    if fails.locked(username):
        raise ApiError(429, "TOO_MANY_ATTEMPTS", "Too many wrong tries. Wait a few minutes and try again.")

    found = find_user(username)
    if found is None:
        dummy_verify(body.password)          # same time as a wrong password
        valid = False
    else:
        valid = verify_password(body.password, found[1])
    if not valid:
        fails.fail(username)
        logger.info("auth: failed login")
        raise ApiError(401, "INVALID_LOGIN", "Wrong username or password.")

    user = found[0]
    try:
        token, expires_at = create_token(user.username, user.role)
    except AuthConfigError:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "Login is not set up on the server (JWT_SECRET).") from None
    fails.clear(username)
    return ok({"token": token, "token_type": "bearer", "expires_at": expires_at.isoformat(),
               "user": user.model_dump()})


@router.get("/me")
def me(user: Annotated[User, Depends(current_user)]):
    return ok(user.model_dump())
