"""Where login looks users up.

1. The database, once Raswanth's db/repo.py provides  get_user(username) -> dict | None
   (keys: username, password_hash, role). Users get there with scripts/seed_users.py, which calls
   repo.upsert_user(username, password_hash, role).
2. Until then, or if the database is down: DEMO_USERS from .env, "name:password:role" separated by commas.
   Passwords are hashed in memory the first time they're needed; the plain text is never kept or logged.

Either way the result is the same: (User, password_hash) or None.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Callable, Optional, get_args

from app.auth.security import hash_password
from app.common.trace import optional
from app.tickets.models import Role, User

logger = logging.getLogger(__name__)

ROLES: tuple[str, ...] = get_args(Role)          # ("analyst", "lead")


class DemoUsersError(ValueError):
    """DEMO_USERS is malformed. The message names the entry number, never its password."""


def parse_demo_users(value: str) -> list[tuple[str, str, str]]:
    """'ana:pw1:analyst, lee:pw2:lead' -> [(username, password, role), ...].
    The password may contain ':' (only the first and last ':' split the entry)."""
    users = []
    for i, entry in enumerate((e.strip() for e in value.split(",")), start=1):
        if not entry:
            continue
        if entry.count(":") < 2:
            raise DemoUsersError(f"DEMO_USERS entry {i} must be name:password:role")
        username, rest = entry.split(":", 1)
        password, role = rest.rsplit(":", 1)
        username, role = username.strip(), role.strip().lower()
        if not username or not password:
            raise DemoUsersError(f"DEMO_USERS entry {i} has an empty name or password")
        if role not in ROLES:
            raise DemoUsersError(f"DEMO_USERS entry {i} has role {role!r}; use one of {', '.join(ROLES)}")
        users.append((username, password, role))
    return users


@lru_cache(maxsize=4)
def _demo_store(value: str) -> dict[str, tuple[User, str]]:
    """Hashed once per DEMO_USERS value (cached), so a changed .env is picked up after a restart."""
    return {u: (User(username=u, role=r), hash_password(p)) for u, p, r in parse_demo_users(value)}


def _db_get_user() -> Optional[Callable[[str], Optional[dict]]]:
    return optional("app.db.repo", "get_user")


def find_user(username: str) -> Optional[tuple[User, str]]:
    """(User, password_hash) for a username, or None if there is no such user."""
    get_user = _db_get_user()
    if get_user is not None:
        try:
            row = get_user(username)
            if row:
                return User(username=row["username"], role=row["role"]), row["password_hash"]
            return None
        except Exception as exc:  # database down: the demo users still let people in
            logger.warning("auth: user lookup in the database failed (%s); using DEMO_USERS",
                           exc.__class__.__name__)
    try:
        return _demo_store(os.getenv("DEMO_USERS", "")).get(username)
    except DemoUsersError as exc:
        logger.error("auth: %s", exc)
        return None
