"""Reference parsers and cleaners for the contract in app/ingest/contract.py (pure functions, no I/O)."""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone


def parse_ts(value: str, system: str) -> datetime | None:
    """Reference parser for the conventions above (used by the validator; None = unparseable)."""
    v = (value or "").strip()
    if not v:
        return None
    try:
        if system == "ServiceNow-EMEA":
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        if system == "Jira-SM":
            return datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc)
        if system == "Email-Gateway":
            return datetime.strptime(v, "%a, %d %b %Y %H:%M:%S %z").astimezone(timezone.utc)
        if system == "Monitoring-Bridge":
            return datetime.fromtimestamp(int(v), tz=timezone.utc)
        if system == "Legacy-Remedy":
            return datetime.strptime(v, "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=timezone(timedelta(hours=-6))) \
                .astimezone(timezone.utc)
    except ValueError:
        return None
    return None


def canon(value: str, table: dict[str, list[str]]) -> str | None:
    v = (value or "").strip()
    for k, aliases in table.items():
        if v.lower() == k.lower() or v in aliases or v.lower() in (a.lower() for a in aliases):
            return k
    return None


def clean_text(value: str) -> str:
    """Reference cleaner for recoverable text noise: mojibake, HTML, entities, whitespace."""
    v = value or ""
    if "â€" in v or "Ã" in v:
        try:
            v = v.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    v = v.replace("’", "'").replace("–", "-")
    v = re.sub(r"<br\s*/?>", " ", v, flags=re.I)
    v = re.sub(r"</?(p|div|span|b|i|font)[^>]*>", " ", v, flags=re.I)
    v = html.unescape(v).replace("\xa0", " ")
    return " ".join(v.split())
