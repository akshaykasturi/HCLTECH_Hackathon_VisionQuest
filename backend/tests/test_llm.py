"""LLM gateway (app/common/llm.py). A fake HTTP layer answers: no test ever calls a real API."""
import json

import pytest
from pydantic import BaseModel

from app.common import llm
from app.common.llm import LLMError, generate_json, parse_json

FAKE_GEMINI = "AIza" + "SyFAKEfakeFAKEfakeFAKEfakeFAKEfake1"
FAKE_GROQ = "gsk_" + "FAKEfakeFAKEfakeFAKEfake01"


class Answer(BaseModel):
    summary: str
    score: float


def gemini_reply(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def groq_reply(text: str) -> dict:
    return {"choices": [{"message": {"content": text}}]}


class FakeHttp:
    """Answers from a list per provider (by URL); records every call."""

    def __init__(self, gemini=(), groq=()):
        self.queues = {"gemini": list(gemini), "groq": list(groq)}
        self.calls: list[dict] = []

    def __call__(self, url, headers, body, timeout):
        name = "gemini" if "generativelanguage" in url else "groq"
        self.calls.append({"provider": name, "url": url, "headers": headers, "body": body, "timeout": timeout})
        reply = self.queues[name].pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", FAKE_GROQ)
    monkeypatch.delenv("LLM_TIMEOUT_S", raising=False)


GOOD = json.dumps({"summary": "Likely the cache.", "score": 0.7})


def test_gemini_answers_first(keys) -> None:
    http = FakeHttp(gemini=[gemini_reply(GOOD)])
    data, provider, errors = generate_json("Explain.", "Case 1", Answer, http=http)
    assert data == Answer(summary="Likely the cache.", score=0.7) and provider == "gemini" and errors == []
    call = http.calls[0]
    assert call["headers"] == {**llm.COMMON_HEADERS, "x-goog-api-key": FAKE_GEMINI} and call["timeout"] == 15.0
    system = call["body"]["systemInstruction"]["parts"][0]["text"]
    assert "Explain." in system and '"summary"' in system  # the schema is in the prompt


def test_falls_back_to_groq_when_gemini_fails(keys) -> None:
    http = FakeHttp(gemini=[LLMError("HTTP 503")], groq=[groq_reply(GOOD)])
    data, provider, errors = generate_json("s", "u", Answer, http=http)
    assert provider == "groq" and data.score == 0.7
    assert errors == ["gemini: HTTP 503"]
    assert http.calls[1]["headers"] == {**llm.COMMON_HEADERS, "Authorization": f"Bearer {FAKE_GROQ}"}
    assert http.calls[1]["body"]["response_format"] == {"type": "json_object"}


def test_bad_json_is_retried_once_with_the_error(keys) -> None:
    http = FakeHttp(gemini=[gemini_reply("not json"), gemini_reply("```json\n" + GOOD + "\n```")])
    data, provider, errors = generate_json("s", "case", Answer, http=http)
    assert provider == "gemini" and data.summary == "Likely the cache."
    assert errors == ["gemini: attempt 1: not valid JSON"]
    retry_prompt = http.calls[1]["body"]["contents"][0]["parts"][0]["text"]
    assert retry_prompt.startswith("case") and "rejected (not valid JSON)" in retry_prompt


def test_schema_errors_count_as_bad_json(keys) -> None:
    wrong = json.dumps({"summary": "x"})  # score missing
    http = FakeHttp(gemini=[gemini_reply(wrong), gemini_reply(wrong)], groq=[groq_reply(GOOD)])
    data, provider, errors = generate_json("s", "u", Answer, http=http)
    assert provider == "groq" and len(http.calls) == 3
    assert errors[0].startswith("gemini: attempt 1: schema error: score")


def test_everything_failing_returns_none_for_the_template_fallback(keys) -> None:
    http = FakeHttp(gemini=[LLMError("timed out after 15 s")], groq=[LLMError("network error (OSError)")])
    data, provider, errors = generate_json("s", "u", Answer, http=http)
    assert (data, provider) == (None, None)
    assert errors == ["gemini: timed out after 15 s", "groq: network error (OSError)"]


def test_providers_without_keys_are_skipped(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    http = FakeHttp()
    assert generate_json("s", "u", Answer, http=http) == (
        None, None, ["gemini: skipped (GEMINI_API_KEY not set)", "groq: skipped (GROQ_API_KEY not set)"])
    assert http.calls == []


def test_errors_and_logs_never_contain_keys_or_secrets(keys, caplog) -> None:
    email = "jane" + "@" + "example.com"
    http = FakeHttp(gemini=[LLMError(f"HTTP 400 for {email} key {FAKE_GEMINI}")], groq=[LLMError("HTTP 401")])
    _, _, errors = generate_json("s", "u", Answer, http=http)
    everything = " ".join(errors) + caplog.text
    assert FAKE_GEMINI not in everything and FAKE_GROQ not in everything and email not in everything


def test_unexpected_reply_shape_is_an_error(keys) -> None:
    http = FakeHttp(gemini=[{"promptFeedback": {"blockReason": "SAFETY"}}], groq=[groq_reply(GOOD)])
    _, provider, errors = generate_json("s", "u", Answer, http=http)
    assert provider == "groq" and errors == ["gemini: unexpected reply shape (no candidates)"]


def test_timeout_comes_from_the_environment(keys, monkeypatch) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", "4")
    http = FakeHttp(gemini=[gemini_reply(GOOD)])
    generate_json("s", "u", Answer, http=http)
    assert http.calls[0]["timeout"] == 4.0
    monkeypatch.setenv("LLM_TIMEOUT_S", "abc")
    assert llm.timeout_s() == 15.0


def test_parse_json_accepts_plain_and_fenced_json() -> None:
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json("ok")


def test_urllib_layer_reports_network_errors_without_details(monkeypatch) -> None:
    import urllib.error

    def refuse(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("secret detail"))

    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    with pytest.raises(LLMError) as exc:
        llm.urllib_post("https://127.0.0.1:9/x", {}, {}, 1.0)
    assert str(exc.value) == "network error (ConnectionRefusedError)"


def test_every_request_sends_our_user_agent(keys) -> None:
    # Cloudflare in front of Groq answers 403 to urllib's default "Python-urllib" User-Agent.
    http = FakeHttp(gemini=[LLMError("HTTP 503")], groq=[groq_reply(GOOD)])
    generate_json("s", "u", Answer, http=http)
    assert [c["provider"] for c in http.calls] == ["gemini", "groq"]
    for call in http.calls:
        assert call["headers"]["User-Agent"] == "hackathon-llm-gateway/1.0"
        assert call["headers"]["Accept"] == "application/json"


def test_urllib_layer_sends_user_agent_not_python_urllib(monkeypatch) -> None:
    seen = {}

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def capture(request, timeout):
        seen["ua"] = request.get_header("User-agent")
        seen["accept"] = request.get_header("Accept")
        return Reply()

    monkeypatch.setattr(llm.urllib.request, "urlopen", capture)
    assert llm.urllib_post("https://127.0.0.1:9/x", {}, {}, 1.0) == {"ok": True}
    assert seen == {"ua": "hackathon-llm-gateway/1.0", "accept": "application/json"}


def test_http_error_shows_status_and_masked_body_but_never_the_key(monkeypatch) -> None:
    import io
    import urllib.error

    email = "jane" + "@" + "example.com"
    body = (f'{{"error": {{"message": "Forbidden for {email}, key {FAKE_GROQ} rejected", '
            f'"padding": "{"x" * 500}"}}}}').encode()

    def forbid(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr(llm.urllib.request, "urlopen", forbid)
    with pytest.raises(LLMError) as exc:
        llm.urllib_post("https://127.0.0.1:9/x", {"Authorization": f"Bearer {FAKE_GROQ}"}, {}, 1.0)
    message = str(exc.value)
    assert message.startswith("HTTP 403: ") and "Forbidden for" in message
    assert FAKE_GROQ not in message and email not in message
    assert len(message) <= len("HTTP 403: ") + llm.ERROR_BODY_CHARS
