"""Test-wide settings: offline and deterministic.

- Doc search runs BM25 only (no embedding model download). The hybrid path is tested with a fake embedder.
- LLM keys are removed so no test can reach a real API (tests pass a fake HTTP function instead).
- The database is a fresh SQLite file in a temp folder (never backend/data/app.db, never Neon): set before
  anything reads DATABASE_URL, and load_dotenv() never overrides a variable that is already set.
"""
import os
import tempfile
from pathlib import Path

import pytest

os.environ["DATABASE_URL"] = f"sqlite:///{(Path(tempfile.mkdtemp(prefix='vq-test-db-')) / 'test.db').as_posix()}"

os.environ["DOC_SEARCH_DENSE"] = "0"
for _name in ("GEMINI_API_KEY", "GROQ_API_KEY"):
    os.environ.pop(_name, None)


@pytest.fixture(scope="session", autouse=True)
def _tables():
    """Create the tables once, so pipeline tests that save a result have somewhere to save it."""
    from app.db.engine import get_engine
    from app.db.tables import Base
    Base.metadata.create_all(get_engine())
