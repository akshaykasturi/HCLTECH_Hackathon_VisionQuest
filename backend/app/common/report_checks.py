"""Code checks the LLM's output before anyone sees it.

The checks work on plain building blocks, so they fit any report shape (tickets/checks.py adapts them to Recommendation):

  Evidence      an item the report may cite: id, text (what the LLM sees and quotes), data (the numbers)
  Citation      evidence_id + a quote copied word for word from that item's text
  Finding       one problem: code, severity ("block" = do not show that part), message, where

  check_citations(citations, evidence)   ids exist and every quote is in the cited text
  check_numbers(text, where, evidence)   every number in the text is found in the (cited) evidence
  check_certainty(text, where)           no "confirmed", "definitely", "the root cause is"... unless negated
  check_safe_steps(action, where)        no changing or destructive actions (restart, delete, roll back...)
  check_secrets(value)                   no token, API key, email or IP anywhere (any dict / list / model)
  groundedness(citations, evidence)      share of citations that pass (0..1)

Every check is pure: it reads, it returns findings, it changes and prints nothing.
Word lists live in report_checks.yaml: change words there, not in code.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import yaml
from pydantic import BaseModel, Field

from app.common.redact import find_leaks

_CHECKS_FILE = Path(__file__).with_name("report_checks.yaml")


class Evidence(BaseModel):
    id: str
    text: str
    data: dict[str, Any] = Field(default_factory=dict)


class Citation(BaseModel):
    evidence_id: str
    quote: str


class Finding(BaseModel):
    code: str  # e.g. "CITE_QUOTE_NOT_FOUND"
    severity: Literal["info", "warn", "block"]
    message: str  # never contains a secret
    where: str  # path in the report, e.g. "summary", "items[0].supporting[1]"


@lru_cache(maxsize=1)
def get_check_settings() -> dict[str, Any]:
    with _CHECKS_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def _block(code: str, message: str, where: str) -> Finding:
    return Finding(code=code, severity="block", message=message, where=where)


def _norm(text: str) -> str:
    """Lower case, one space between words: so quotes match whatever the spacing or case."""
    return " ".join(text.lower().split())


def _by_id(evidence: Iterable[Evidence]) -> dict[str, Evidence]:
    return {item.id: item for item in evidence}


def has_blocks(findings: Iterable[Finding]) -> bool:
    return any(f.severity == "block" for f in findings)


# ---------- citations ----------

def citation_problem(citation: Citation, by_id: dict[str, Evidence]) -> Optional[tuple[str, str]]:
    """(code, message) if the citation cannot be verified, None if it is fine."""
    item = by_id.get(citation.evidence_id)
    if item is None:
        return "CITE_UNKNOWN_ID", f"evidence id {citation.evidence_id!r} does not exist"
    q = _norm(citation.quote)
    if len(q) < get_check_settings()["citations"]["min_quote_chars"]:
        return "CITE_QUOTE_TOO_SHORT", "the quote is too short to show anything"
    if q not in _norm(item.text):
        return "CITE_QUOTE_NOT_FOUND", f"the quote is not in the text of {citation.evidence_id!r}"
    return None


def check_citations(citations: list[tuple[str, Citation]], evidence: list[Evidence]) -> list[Finding]:
    """citations: (where, Citation) pairs, e.g. ("items[0].supporting[1]", Citation(...))."""
    by_id = _by_id(evidence)
    return [_block(*problem, where) for where, c in citations if (problem := citation_problem(c, by_id))]


def groundedness(citations: list[Citation], evidence: list[Evidence]) -> Optional[float]:
    """Share of citations that pass the citation check (0..1). None when nothing is cited."""
    if not citations:
        return None
    by_id = _by_id(evidence)
    return sum(1 for c in citations if citation_problem(c, by_id) is None) / len(citations)


# ---------- numbers ----------

# Times (05:17 or 05:17:22) and dates (2026-10-15, with an optional time) are checked as text.
_TIME_RE = re.compile(r"(?<![\w:])\d{1,2}:\d{2}(?::\d{2})?(?![\w:])")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?")
# Paths like /v1/items/{id}: their digits are not numbers to check.
_PATH_RE = re.compile(r"/[\w/{}.-]*")
# A number: 1,234 or 1234 or 12.5, an optional "x" in front (x8.27), an optional % and unit.
# Digits glued to letters or dashes (p95, v1, top-3) are part of a name: skipped.
_NUMBER_RE = re.compile(
    r"(?<![\w.,/:-])[x×]?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?(?P<pct>\s?%)?"
    r"(?P<tail>[A-Za-z_]*+)(?![\d-])(?:\s*(?P<unit>ms|seconds|secs|sec|s|minutes|mins|min)\b)?"
)
_SECONDS = {"s", "sec", "secs", "seconds"}
_MINUTES = {"min", "mins", "minutes"}


def _numbers(text: str) -> list[dict[str, Any]]:
    """Numbers in a piece of text: value, decimals shown, percent?, unit."""
    out = []
    for m in _NUMBER_RE.finditer(text):
        tail = m.group("tail").lower()
        unit = m.group("unit") or (tail if tail in _SECONDS | _MINUTES | {"ms"} else None)
        if tail and tail != unit:
            continue  # e.g. "2xx" or "1e93f": part of a name, not a number
        frac = m.group("frac") or ""
        out.append({"text": m.group(0).strip(), "value": float(m.group("int").replace(",", "") + frac),
                    "decimals": max(len(frac) - 1, 0), "pct": bool(m.group("pct")), "unit": unit})
    return out


def _data_values(value: Any) -> Iterable[float]:
    """Every number inside an evidence item's data (dict keys too: {"504": 17})."""
    if isinstance(value, bool) or value is None:
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, str):
        try:
            yield float(value)
        except ValueError:
            pass
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _data_values(k)
            yield from _data_values(v)
    elif isinstance(value, list):
        for v in value:
            yield from _data_values(v)


def _pool(items: list[Evidence]) -> tuple[list[float], str]:
    """All numbers found in these items (text and data), and their text for times and dates."""
    values: list[float] = []
    for item in items:
        for n in _numbers(item.text):
            values.append(n["value"])
            if n["pct"]:
                values.append(n["value"] / 100)
        values.extend(_data_values(item.data))
    text = " ".join(item.text + " " + json.dumps(item.data, default=str) for item in items)
    return values, text


def _matches(n: dict[str, Any], pool: list[float]) -> bool:
    """True if the number (in any of its readings: %, s vs ms, min vs s) is in the pool."""
    v = n["value"]
    half = 0.5 * 10 ** -n["decimals"]  # "161" covers 160.5..161.5; "38.6" covers 38.55..38.65
    readings = [(v, half)]
    if n["pct"]:
        readings.append((v / 100, half / 100))  # 12.5% vs 0.125
    if n["unit"] in _SECONDS:
        readings.append((v * 1000, half * 1000))  # 10 s vs 10,000 ms
    if n["unit"] == "ms":
        readings.append((v / 1000, half / 1000))
    if n["unit"] in _MINUTES:
        readings.append((v * 60, half * 60))  # 60 min vs 3600 s
    return any(abs(r - e) <= tol + 1e-9 for r, tol in readings for e in pool)


def check_numbers(text: str, where: str, evidence: list[Evidence],
                  cited_ids: Optional[Iterable[str]] = None, also_allowed: Iterable[float] = ()) -> list[Finding]:
    """Every number, date and time in `text` must come from the evidence.

    cited_ids: only these items count (e.g. what an explanation cites); None means all evidence.
    also_allowed: numbers that are names, not measurements (e.g. HTTP status codes).
    Numbers are compared after rounding to the digits shown: "161 ms" matches 160.73, "503" does not match 504.
    """
    allowed = {float(x) for x in get_check_settings()["numbers"]["always_allowed"]} | {float(x) for x in also_allowed}
    by_id = _by_id(evidence)
    items = evidence if cited_ids is None else [by_id[i] for i in dict.fromkeys(cited_ids) if i in by_id]
    pool, pool_text = _pool(items)
    findings = []
    # Evidence ids and paths carry digits that are names, not numbers: remove them first.
    for item in sorted(evidence, key=lambda e: -len(e.id)):
        text = text.replace(item.id, " ")
    text = _PATH_RE.sub(" ", text)
    for pattern in (_DATE_RE, _TIME_RE):
        for m in pattern.finditer(text):
            if m.group(0) not in pool_text:
                findings.append(_block("NUMBER_NOT_IN_EVIDENCE", f"{m.group(0)} is not in the evidence", where))
        text = pattern.sub(" ", text)
    for n in _numbers(text):
        if n["value"] in allowed and not n["pct"] and not n["unit"]:
            continue
        if not _matches(n, pool):
            findings.append(_block("NUMBER_NOT_IN_EVIDENCE", f"{n['text']} is not in the evidence", where))
    return findings


# ---------- certainty ----------

def _phrase_re(phrase: str) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(w) for w in phrase.lower().split())
    return re.compile(r"(?<!\w)" + words + r"(?!\w)")


def _is_negated(text: str, start: int, end: int) -> bool:
    cfg = get_check_settings()["certainty"]
    negations = set(cfg["negations"])
    before = re.findall(r"[\w']+", text[:start])[-cfg["words_before"]:]
    after = re.findall(r"[\w']+", text[end:])[:cfg["words_after"]]
    return any(w in negations for w in before + after)


def check_certainty(text: str, where: str) -> list[Finding]:
    """Block "confirmed", "definitely"... (unless negated: "not confirmed" is fine)."""
    low = _norm(text)
    findings = []
    for phrase in get_check_settings()["certainty"]["phrases"]:
        for m in _phrase_re(phrase).finditer(low):
            if not _is_negated(low, m.start(), m.end()):
                findings.append(_block("CERTAINTY", f"says {phrase!r}: only the evidence or a reviewer confirms",
                                       where))
    return findings


# ---------- safe steps ----------

def _verb_re(verb: str) -> str:
    """Match a verb in any simple form: restart(s/ed/ing), rotate/rotating, drop/dropped/dropping."""
    *first, last = verb.split()
    stem = last[:-1] if last.endswith("e") else last
    double = re.escape(last[-1]) + "?" if not last.endswith("e") else ""
    words = r"\s+".join(re.escape(w) for w in first + [stem])
    ending = r"(?:e|es|ed|ing)" if last.endswith("e") else double + r"(?:s|es|ed|d|ing)?"
    return r"(?<!\w)" + words + ending + r"(?!\w)"


def check_safe_steps(action: str, where: str) -> list[Finding]:
    """A suggested step must only look (read, compare, query); changing actions go to a human."""
    low = _norm(action)
    for verb in get_check_settings()["safe_steps"]["verbs"]:
        if re.search(_verb_re(verb), low):
            return [_block("UNSAFE_STEP", f"{verb!r} changes the system: a human must decide", where)]
    return []


# ---------- secrets ----------

def _strings(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _strings(v, f"{path}.{k}" if path else str(k))
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            yield from _strings(v, f"{path}[{i}]")


def check_secrets(value: Any, where: str = "") -> list[Finding]:
    """Every string inside value (str, dict, list or Pydantic model) goes through redact.find_leaks.
    Only the kind of secret is reported, never the value."""
    return [_block("SECRET", f"contains a secret ({', '.join(kinds)})", path or "value")
            for path, text in _strings(value, where) if (kinds := find_leaks(text))]
