"""Step 1 (with classify): screen the RAW ticket, in memory only, before anything else happens.

    result = screen(ticket)      # ScreenResult(blocked, safety, codes)

- blocked: an attack on the assistant itself (prompt injection, role override, asking for someone else's
  password, dumping other users' data). The gate turns this into BLOCKED: no search, no LLM.
- safety:  a physical hazard (smoke, sparks, swollen battery, spill). The gate escalates it as P1.

A ticket ABOUT security ("I got a phishing email", "my account is locked") is a normal ticket, not an attack.

Rules:
- This step sees raw text, so it never logs, stores or returns any of it: only reason codes come out.
- Patterns live in screen.yaml: change words there, not here.
- Text is normalised first (Unicode NFKC, zero-width characters removed, lower case, single spaces), so
  look-alike letters and hidden characters don't slip past the patterns.
- Base64 blobs are decoded and screened too, so "decode this: aWdub3Jl..." can't hide an instruction.
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.tickets.models import ScreenResult, TicketIn

_RULES_FILE = Path(__file__).with_name("screen.yaml")
# Zero-width and direction-control characters: invisible, but they split words so patterns miss them.
_INVISIBLE_RE = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
# A run of 16+ base64 characters (with optional padding): long enough to hide a short instruction.
_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=])")


@lru_cache(maxsize=1)
def _rules() -> dict[str, dict[str, list[re.Pattern[str]]]]:
    with _RULES_FILE.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)
    return {group: {code: [re.compile(p) for p in patterns] for code, patterns in codes.items()}
            for group, codes in raw.items()}


def normalise(text: str) -> str:
    """NFKC (full-width and look-alike forms -> plain letters), drop invisible characters,
    lower case, one space between words."""
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE_RE.sub("", text)
    return " ".join(text.lower().split())


def decoded_base64(text: str) -> list[str]:
    """Readable text hidden in base64 runs. Runs that don't decode to mostly printable text are skipped
    (ordinary long words and IDs decode to binary noise)."""
    found = []
    for m in _BASE64_RE.finditer(text):
        blob = m.group(0)
        try:
            raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
        except (binascii.Error, ValueError):
            continue
        decoded = raw.decode("utf-8", errors="ignore")
        if len(decoded) >= 8 and sum(c.isprintable() or c.isspace() for c in decoded) / len(decoded) > 0.95:
            found.append(decoded)
    return found


def _ticket_text(ticket: TicketIn) -> str:
    parts = [ticket.subject, ticket.description, ticket.affected_service, ticket.environment, ticket.channel]
    raw = " \n ".join(p for p in parts if p)
    return normalise(" \n ".join([raw, *decoded_base64(raw)]))


def _matching_codes(text: str, group: str) -> list[str]:
    return [code for code, patterns in _rules()[group].items() if any(p.search(text) for p in patterns)]


def screen(ticket: TicketIn) -> ScreenResult:
    """Reason codes only: the raw text never leaves this function."""
    text = _ticket_text(ticket)
    blocked = _matching_codes(text, "blocked")
    safety = _matching_codes(text, "safety")
    return ScreenResult(blocked=bool(blocked), safety=bool(safety), codes=blocked + safety)
