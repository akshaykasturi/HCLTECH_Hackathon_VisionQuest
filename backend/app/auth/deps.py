"""FastAPI dependencies for routes that need a signed-in user.

    from app.auth.deps import current_user, require_role

    @router.get("/history")
    def history(user: Annotated[User, Depends(current_user)]): ...

    @router.get("/stats")
    def stats(user: Annotated[User, Depends(require_role("lead"))]): ...

The request must send  Authorization: Bearer <token>  (the token from POST /api/auth/login).
No token, a broken or expired token -> 401. Signed in but the wrong role -> 403.
A lead can do everything an analyst can.

Tests for other routes can skip login with FastAPI's override:
    app.dependency_overrides[current_user] = lambda: User(username="test", role="analyst")
"""
from __future__ import annotations

from typing import Annotated, Callable, Optional, get_args

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import AuthConfigError, InvalidToken, decode_token
from app.common.api import ApiError
from app.tickets.models import Role, User

# Higher number = more rights. A role may do anything a lower role may.
ROLE_LEVEL = {"analyst": 1, "lead": 2}
assert set(ROLE_LEVEL) == set(get_args(Role)), "ROLE_LEVEL must list every Role"

_bearer = HTTPBearer(auto_error=False)


def current_user(credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(_bearer)]) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise ApiError(401, "UNAUTHORIZED", "Sign in first: send Authorization: Bearer <token>.")
    try:
        claims = decode_token(credentials.credentials)
    except InvalidToken as exc:
        raise ApiError(401, "UNAUTHORIZED", f"{exc}.") from None
    except AuthConfigError:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "Login is not set up on the server (JWT_SECRET).") from None
    if claims["role"] not in ROLE_LEVEL:
        raise ApiError(401, "UNAUTHORIZED", "the login token is not valid.")
    return User(username=claims["sub"], role=claims["role"])


def require_role(role: str) -> Callable[..., User]:
    """A dependency that lets in `role` and any higher role."""
    if role not in ROLE_LEVEL:
        raise ValueError(f"unknown role {role!r}")

    def check(user: Annotated[User, Depends(current_user)]) -> User:
        if ROLE_LEVEL[user.role] < ROLE_LEVEL[role]:
            raise ApiError(403, "FORBIDDEN", f"This needs the {role} role.")
        return user

    return check
