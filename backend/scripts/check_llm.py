"""Check our real LLM keys: ask each provider to "say ok" and print what happened.

Run from the backend folder (keys come from backend/.env or the environment):
    .\\.venv\\Scripts\\python.exe scripts\\check_llm.py

Prints only the provider, OK / FAILED and a short reason. Never prints a key.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.common.env import load_dotenv  # noqa: E402
from app.common.llm import PROVIDERS, LLMError, complete_text  # noqa: E402
from app.common.redact import mask  # noqa: E402


def main() -> int:
    load_dotenv()
    failures = 0
    for provider in PROVIDERS:
        if provider.key() is None:
            print(f"{provider.name:7} SKIPPED  ({provider.key_env} is not set)")
            continue
        started = time.perf_counter()
        try:
            reply = complete_text(provider, "You are a connectivity check.",
                                  'Say ok. Reply with exactly this JSON: {"reply": "ok"}')
        except LLMError as exc:
            failures += 1
            print(f"{provider.name:7} FAILED   {mask(str(exc))}")
            continue
        ms = (time.perf_counter() - started) * 1000
        status = "OK" if "ok" in reply.lower() else "ODD REPLY"
        print(f"{provider.name:7} {status:8} {ms:.0f} ms, reply: {mask(reply.strip())[:80]}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
