"""Test-wide settings: offline and deterministic.

- Doc search runs BM25 only (no embedding model download). The hybrid path is tested with a fake embedder.
- LLM keys are removed so no test can reach a real API (tests pass a fake HTTP function instead).
"""
import os

os.environ["DOC_SEARCH_DENSE"] = "0"
for _name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
    os.environ.pop(_name, None)
