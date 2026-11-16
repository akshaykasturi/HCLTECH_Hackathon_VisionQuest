"""Secret masking: hide secrets before anything else reads, logs, stores or shows the text.

Rule: keep what is useful, hide the secret.

- Bearer tokens (JWTs)   -> "Bearer <TOKEN>"  (a JWT with no "Bearer" in front -> "<TOKEN>")
- API keys               -> the prefix stays, the secret part goes: "sk-<KEY>", "gsk_<KEY>", "AIza<KEY>",
                            "<word>_live_<KEY>" / "<word>_test_<KEY>" (a test key used on prod is a useful fact)
- emails                 -> "<EMAIL>"
- IPv4 addresses         -> keep the /24, hide the host: 203.0.113.24 -> "203.0.113.x"
- Idempotency-Key values -> "idem_<first 8 hex of sha256>": the same key always gives the same hash
- headers by name        -> Authorization, X-Api-Key, Idempotency-Key are masked whatever their value looks like

Add a pattern to _KEY_PATTERNS when tomorrow's data has its own key format.
Standard library only. Nothing here prints or logs a secret.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from typing import Any

# Kinds of secret, used as keys in the counts and in find_leaks().
BEARER = "bearer_token"
KEY_LIVE = "api_key_live"
KEY_TEST = "api_key_test"
KEY_OTHER = "api_key_other"
EMAIL = "email"
IP = "ip"
IDEM = "idempotency_key"

# A JWT is three base64url parts separated by dots; its header always starts with "eyJ".
_JWT = r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?"
# "Bearer <something long>": 16+ characters so normal words ("Bearer token expired") are kept.
_BEARER_RE = re.compile(r"\bBearer\s+(?:" + _JWT + r"|[A-Za-z0-9._~+/-]{16,}=*)", re.IGNORECASE)
_BARE_JWT_RE = re.compile(_JWT)
# "<prefix>_live_<secret>" / "<prefix>_test_<secret>" (Stripe-style keys). Group 1: prefix, group 2: live/test.
_ENV_KEY_RE = re.compile(r"\b([A-Za-z]{2,10})_(live|test)_[A-Za-z0-9]{8,}")
# Known provider keys: (regex, kept prefix). The secret part must be long, so "sk-learn" is not a key.
_KEY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}"), "sk-"),    # OpenAI / Anthropic style
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), "gsk_"),                    # Groq
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "AIza"),                  # Google / Gemini
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "gh_"),               # GitHub tokens
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA"),                      # AWS access key id
]
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Four numbers 0-999 with dots, not part of a longer dotted number (like a version 1.2.3.4.5).
_IP_RE = re.compile(r"(?<![\d.])(\d{1,3}\.\d{1,3}\.\d{1,3})\.(\d{1,3})(?!\.?\d)")

# Headers masked by their name, not by pattern (compared lower-case).
_AUTH_HEADER = "authorization"
_API_KEY_HEADER = "x-api-key"
_IDEM_HEADER = "idempotency-key"


def _is_ip(first_three: str, last: str) -> bool:
    return all(int(part) <= 255 for part in [*first_three.split("."), last])


def mask_text(text: str) -> tuple[str, dict[str, int]]:
    """Mask every secret found in a piece of text. Returns (masked text, counts per kind)."""
    counts: Counter[str] = Counter()

    def sub(pattern: re.Pattern[str], kind_of, replacement_of, s: str) -> str:
        def repl(m: re.Match[str]) -> str:
            kind = kind_of(m)
            if kind is None:  # not a secret after all (e.g. 999.1.2.3 is not an IP)
                return m.group(0)
            counts[kind] += 1
            return replacement_of(m)
        return pattern.sub(repl, s)

    # Order matters: tokens first, so "Bearer eyJ..." is counted once, not as a bare JWT too.
    text = sub(_BEARER_RE, lambda m: BEARER, lambda m: "Bearer <TOKEN>", text)
    text = sub(_BARE_JWT_RE, lambda m: BEARER, lambda m: "<TOKEN>", text)
    text = sub(_ENV_KEY_RE, lambda m: KEY_LIVE if m.group(2) == "live" else KEY_TEST,
               lambda m: f"{m.group(1)}_{m.group(2)}_<KEY>", text)
    for pattern, prefix in _KEY_PATTERNS:
        text = sub(pattern, lambda m: KEY_OTHER, lambda m, p=prefix: f"{p}<KEY>", text)
    text = sub(_EMAIL_RE, lambda m: EMAIL, lambda m: "<EMAIL>", text)
    text = sub(_IP_RE, lambda m: IP if _is_ip(m.group(1), m.group(2)) else None,
               lambda m: f"{m.group(1)}.x", text)
    return text, dict(counts)


def mask(text: str) -> str:
    """Masked copy of a string (counts dropped). Use this before logging anything."""
    return mask_text(text)[0]


def hash_idempotency_key(value: str) -> str:
    """Same key -> same short hash; the raw value cannot be read back."""
    return "idem_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _mask_header(name: str, value: str, counts: Counter[str]) -> str | None:
    """Mask a sensitive header by its name. Returns None if the header is not one of them."""
    lname = name.lower()
    if lname == _AUTH_HEADER:
        counts[BEARER] += 1
        scheme = value.split(" ", 1)[0] if " " in value.strip() else "Bearer"
        return f"{scheme} <TOKEN>"
    if lname == _API_KEY_HEADER:
        m = _ENV_KEY_RE.fullmatch(value.strip())
        if m:
            counts[KEY_LIVE if m.group(2) == "live" else KEY_TEST] += 1
            return f"{m.group(1)}_{m.group(2)}_<KEY>"
        counts[KEY_OTHER] += 1  # any other key format: hide all of it
        return "<KEY>"
    if lname == _IDEM_HEADER:
        counts[IDEM] += 1
        return hash_idempotency_key(value)
    return None


def _walk(value: Any, counts: Counter[str]) -> Any:
    """Mask a copied value in place, going into dicts and lists. Returns the masked value."""
    if isinstance(value, dict):
        for k, v in value.items():
            masked = _mask_header(k, v, counts) if isinstance(k, str) and isinstance(v, str) else None
            value[k] = masked if masked is not None else _walk(v, counts)
        return value
    if isinstance(value, list):
        for i, v in enumerate(value):
            value[i] = _walk(v, counts)
        return value
    if isinstance(value, str):
        masked, found = mask_text(value)
        counts.update(found)
        return masked
    return value  # numbers, booleans, None: never secret


def mask_record(record: dict) -> tuple[dict, dict[str, int]]:
    """Mask a whole record (e.g. one log line). Works on a deep copy: the input is never changed.

    Authorization, X-Api-Key and Idempotency-Key are masked by name (wherever they appear);
    every other string goes through mask_text. Dict keys and non-string values are kept.
    """
    counts: Counter[str] = Counter()
    masked = _walk(copy.deepcopy(record), counts)
    return masked, dict(counts)


# Patterns that mean a raw secret is still there. Masked forms ("Bearer <TOKEN>", "sk-<KEY>",
# "<EMAIL>", "203.0.113.x") do not match any of them.
_LEAK_CHECKS: list[tuple[str, re.Pattern[str]]] = [
    (BEARER, _BEARER_RE),
    (BEARER, _BARE_JWT_RE),
    (KEY_LIVE, re.compile(r"\b[A-Za-z]{2,10}_live_[A-Za-z0-9]{8,}")),
    (KEY_TEST, re.compile(r"\b[A-Za-z]{2,10}_test_[A-Za-z0-9]{8,}")),
    *((KEY_OTHER, pattern) for pattern, _ in _KEY_PATTERNS),
    (EMAIL, _EMAIL_RE),
]


def find_leaks(text: str) -> list[str]:
    """Return the kinds of secret still present in text (sorted, no repeats; [] means clean).

    Only kinds are returned, never the matched values, so callers can report safely.
    """
    kinds = {kind for kind, pattern in _LEAK_CHECKS if pattern.search(text)}
    if any(_is_ip(m.group(1), m.group(2)) for m in _IP_RE.finditer(text)):
        kinds.add(IP)
    return sorted(kinds)
