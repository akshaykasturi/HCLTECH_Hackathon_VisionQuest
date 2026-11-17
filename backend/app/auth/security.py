"""Passwords and login tokens. Standard library + PyJWT (already in requirements.txt).

Passwords: PBKDF2-HMAC-SHA256 with a random 16-byte salt, stored as one string:
    pbkdf2_sha256$<iterations>$<salt, base64>$<hash, base64>
The real password is never stored, logged or returned. Checking uses a constant-time compare.

Tokens: a JWT signed with HS256 and JWT_SECRET (from .env, at least 32 characters). It holds the username
(sub), the role, when it was made (iat) and when it ends (exp, JWT_EXPIRE_HOURS, default 8).
A token can't be changed without the secret: changing the role inside it breaks the signature.

Environment:
    JWT_SECRET                 required for login; long and random, never a real password
    JWT_EXPIRE_HOURS           default 8
    PASSWORD_HASH_ITERATIONS   default 600000 (OWASP 2023 advice for PBKDF2-SHA256); tests use fewer
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

ALGORITHM = "HS256"
ISSUER = "visionquest-tickets"
MIN_SECRET_CHARS = 32
DEFAULT_ITERATIONS = 600_000
DEFAULT_EXPIRE_HOURS = 8.0
_SCHEME = "pbkdf2_sha256"


class AuthConfigError(Exception):
    """Login can't work: JWT_SECRET is missing or too short. The message never contains the secret."""


class InvalidToken(Exception):
    """The token is missing, broken, signed with another secret, or expired. Message is safe to show."""


# ---------- passwords ----------

def _iterations() -> int:
    try:
        value = int(os.getenv("PASSWORD_HASH_ITERATIONS", DEFAULT_ITERATIONS))
    except ValueError:
        return DEFAULT_ITERATIONS
    return value if value >= 1000 else DEFAULT_ITERATIONS


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, *, iterations: int | None = None) -> str:
    n = iterations or _iterations()
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, n)
    return f"{_SCHEME}${n}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """True if password matches the stored hash. A malformed stored value is simply False."""
    try:
        scheme, n, salt_b64, digest_b64 = stored.split("$")
        if scheme != _SCHEME:
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(n))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# A hash nobody's password matches: checked when the username is unknown, so a wrong username takes as
# long as a wrong password and an attacker can't tell which usernames exist.
_DUMMY_HASH: str | None = None


def dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(16))
    verify_password(password, _DUMMY_HASH)


# ---------- tokens ----------

def jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < MIN_SECRET_CHARS:
        raise AuthConfigError(f"JWT_SECRET is not set or shorter than {MIN_SECRET_CHARS} characters")
    return secret


def expire_hours() -> float:
    try:
        value = float(os.getenv("JWT_EXPIRE_HOURS", DEFAULT_EXPIRE_HOURS))
    except ValueError:
        return DEFAULT_EXPIRE_HOURS
    return value if value > 0 else DEFAULT_EXPIRE_HOURS


def create_token(username: str, role: str, *, now: datetime | None = None) -> tuple[str, datetime]:
    """(token, expires_at). Raises AuthConfigError if JWT_SECRET is not usable."""
    issued = now or datetime.now(timezone.utc)
    expires = issued + timedelta(hours=expire_hours())
    claims = {"sub": username, "role": role, "iat": issued, "exp": expires, "iss": ISSUER}
    return jwt.encode(claims, jwt_secret(), algorithm=ALGORITHM), expires


def decode_token(token: str) -> dict[str, Any]:
    """The checked claims. Raises InvalidToken (or AuthConfigError if the server has no secret)."""
    secret = jwt_secret()
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=ISSUER, leeway=30,
                            options={"require": ["sub", "role", "exp", "iat", "iss"]})
    except jwt.ExpiredSignatureError:
        raise InvalidToken("the login has expired: sign in again") from None
    except jwt.PyJWTError:
        raise InvalidToken("the login token is not valid") from None
    if not isinstance(claims.get("sub"), str) or not isinstance(claims.get("role"), str):
        raise InvalidToken("the login token is not valid")
    return claims
