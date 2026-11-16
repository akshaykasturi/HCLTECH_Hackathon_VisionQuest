"""A small LLM gateway: ask for JSON, get back a checked Pydantic object, or None.

    data, provider, errors = generate_json(system, user, MySchema)
    if data is None:
        ... use the template fallback (code writes the text) and show `errors` in the trace

Order: Gemini (GEMINI_API_KEY), then Groq (GROQ_API_KEY, OpenAI-compatible endpoint), then None.
A provider without a key is skipped. Each call has a timeout (LLM_TIMEOUT_S, default 15 seconds).
The reply is validated against the schema; on bad JSON or a schema error we retry ONCE with the error
attached, then move on to the next provider.

Rules:
- Python's built-in urllib only, no SDKs (no new dependency).
- Keys are read from the environment and only ever put in a request header. They are never logged,
  returned or put in an error message; every error and log line goes through redact.mask first.
- The LLM only writes words. Code decides, and code checks the output (report_checks.py) before anyone sees it.
- Tests pass a fake `http` function: they never call real APIs.

Model names: GEMINI_MODEL (default gemini-2.5-flash), GROQ_MODEL (default llama-3.3-70b-versatile).
Try your keys: `.\\.venv\\Scripts\\python.exe scripts\\check_llm.py`
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from app.common.redact import mask

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
# http(url, headers, body, timeout_s) -> parsed JSON reply. Raises LLMError on any failure.
HttpPost = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]

DEFAULT_TIMEOUT_S = 15.0
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Sent on every request. Cloudflare (in front of api.groq.com) answers 403 to urllib's default
# "Python-urllib/3.x" User-Agent, so we name our client explicitly.
COMMON_HEADERS = {"User-Agent": "hackathon-llm-gateway/1.0", "Accept": "application/json"}
ERROR_BODY_CHARS = 200


class LLMError(Exception):
    """A provider call failed (network, timeout, HTTP error, unexpected reply). Message is safe to show."""


def timeout_s() -> float:
    try:
        value = float(os.getenv("LLM_TIMEOUT_S", DEFAULT_TIMEOUT_S))
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return value if value > 0 else DEFAULT_TIMEOUT_S


def urllib_post(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    """POST JSON with urllib and return the parsed JSON reply."""
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", **COMMON_HEADERS, **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https URLs)
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = _error_body(exc, secrets=list(headers.values()))
        raise LLMError(f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}") from None
    except (TimeoutError, socket.timeout):
        raise LLMError(f"timed out after {timeout:g} s") from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise LLMError(f"timed out after {timeout:g} s") from None
        raise LLMError(f"network error ({type(reason).__name__})") from None
    except (ValueError, UnicodeDecodeError):
        raise LLMError("reply is not JSON") from None


def _error_body(exc: urllib.error.HTTPError, secrets: list[str]) -> str:
    """The start of an HTTP error reply, masked: tells us the real reason (bad key, blocked, quota...).

    Header values (which hold the key) are removed first, then redact.mask runs, then we cut to
    ERROR_BODY_CHARS. Masking before cutting means a half-cut key can never slip past the patterns.
    """
    try:
        text = exc.read(4096).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 (the body is only a hint; never let it hide the status code)
        return ""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<KEY>")
            if secret.startswith("Bearer "):
                text = text.replace(secret[len("Bearer "):], "<KEY>")
    text = " ".join(mask(text).split())
    return text[:ERROR_BODY_CHARS]


# ---------- providers ----------

@dataclass(frozen=True)
class Provider:
    name: str
    key_env: str
    call: Callable[[str, str, str, HttpPost, float], str]  # (key, system, user, http, timeout) -> reply text

    def key(self) -> Optional[str]:
        value = os.getenv(self.key_env, "").strip()
        return value or None


def _call_gemini(key: str, system: str, user: str, http: HttpPost, timeout: float) -> str:
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    reply = http(GEMINI_URL.format(model=model), {**COMMON_HEADERS, "x-goog-api-key": key}, body, timeout)
    try:
        return "".join(p.get("text", "") for p in reply["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply shape (no candidates)") from None


def _call_groq(key: str, system: str, user: str, http: HttpPost, timeout: float) -> str:
    model = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    body = {
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    reply = http(GROQ_URL, {**COMMON_HEADERS, "Authorization": f"Bearer {key}"}, body, timeout)
    try:
        return reply["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError("unexpected reply shape (no choices)") from None


PROVIDERS: list[Provider] = [
    Provider("gemini", "GEMINI_API_KEY", _call_gemini),
    Provider("groq", "GROQ_API_KEY", _call_groq),
]


# ---------- JSON handling ----------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_json(text: str) -> Any:
    """Parse the reply, allowing a ```json fence around it. Raises ValueError if it is not JSON."""
    m = _FENCE_RE.match(text)
    return json.loads(m.group(1) if m else text)


def _system_prompt(system: str, schema: type[BaseModel]) -> str:
    return (f"{system}\n\nReply with ONE JSON object and nothing else. "
            f"It must match this JSON schema:\n{json.dumps(schema.model_json_schema())}")


def _short(error: Exception) -> str:
    """A short, masked description of a parse or validation error (no echo of the reply)."""
    if isinstance(error, ValidationError):
        parts = [f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in error.errors()[:3]]
        return "schema error: " + "; ".join(parts)
    return "not valid JSON"


def generate_json(system: str, user: str, schema: type[T], *, http: Optional[HttpPost] = None,
                  providers: Optional[list[Provider]] = None) -> tuple[Optional[T], Optional[str], list[str]]:
    """Ask each provider in turn for JSON matching `schema`.

    Returns (data, provider name, errors). data is None when every provider failed or none has a key:
    the caller then uses its template fallback. `errors` lists what went wrong, masked and safe to show.
    """
    http = http or urllib_post
    timeout = timeout_s()
    errors: list[str] = []
    full_system = _system_prompt(system, schema)
    for provider in providers if providers is not None else PROVIDERS:
        key = provider.key()
        if key is None:
            errors.append(f"{provider.name}: skipped ({provider.key_env} not set)")
            continue
        prompt = user
        for attempt in (1, 2):
            try:
                text = provider.call(key, full_system, prompt, http, timeout)
            except LLMError as exc:
                errors.append(mask(f"{provider.name}: {exc}"))
                break  # network / HTTP problem: no point retrying this provider
            try:
                return schema.model_validate(parse_json(text)), provider.name, errors
            except (ValueError, ValidationError) as exc:
                problem = _short(exc)
                errors.append(mask(f"{provider.name}: attempt {attempt}: {problem}"))
                prompt = (f"{user}\n\nYour previous reply was rejected ({problem}). "
                          "Reply again with only the JSON object.")
    for line in errors:
        logger.warning("llm: %s", mask(line))
    return None, None, errors


def complete_text(provider: Provider, system: str, user: str, http: Optional[HttpPost] = None) -> str:
    """One plain call to one provider (used by scripts/check_llm.py). Raises LLMError."""
    key = provider.key()
    if key is None:
        raise LLMError(f"{provider.key_env} not set")
    return provider.call(key, system, user, http or urllib_post, timeout_s())
