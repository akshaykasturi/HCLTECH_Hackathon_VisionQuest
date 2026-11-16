"""Doc search (app/common/doc_search.py) on a small synthetic docs folder. BM25 only, plus a fake embedder."""
import hashlib
from pathlib import Path

import numpy as np
import pytest

from app.common import doc_search
from app.common.doc_search import build_index, first_sentences, get_section, search, tokenize
from app.common.docs_loader import load_docs

FRONT = "---\ntitle: {title}\nversion: \"{version}\"\nstatus: {status}\n---\n"
DOCS = {
    "limits.md": FRONT.format(title="Limits", version="2.0", status="current")
    + "# Limits\n\n## Request limit\n\nEach key may send 100 requests per minute. Over the limit you get "
      "429 Too Many Requests with ERR_RATE_LIMITED.\n\n## Payload size\n\nBodies above 1 MB are refused.",
    "timeouts.md": FRONT.format(title="Timeouts", version="2.0", status="current")
    + "# Timeouts\n\n## Gateway timeout\n\nThe gateway waits 15 seconds, then answers 504 ERR_UPSTREAM_TIMEOUT.",
    "legacy/limits-v1.md": FRONT.format(title="Old limits", version="1.0", status="deprecated")
    + "# Old limits\n\n## Request limit\n\nEach key may send 60 requests per minute (rate limit).",
}
DEPRECATED = "legacy/limits-v1"


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("docs")
    for rel, text in DOCS.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def index(folder):
    return build_index(load_docs(folder))


def test_tokenizer_keeps_codes_and_numbers() -> None:
    tokens = tokenize("429 Too Many Requests ERR_RATE_LIMITED on GET /v1/items/{id}")
    assert "429" in tokens and "err_rate_limited" in tokens
    assert {"rate", "limited"} <= set(tokens)  # parts too, so prose "rate limit" still matches
    assert "on" not in tokens  # stop word


def test_tests_run_offline_with_bm25(index) -> None:
    assert index.mode == "bm25"  # conftest.py switches embeddings off


def test_best_section_comes_first(index) -> None:
    hit = search("429 ERR_RATE_LIMITED", k=1, index=index)[0]
    assert hit.section_id == "doc:limits#1" and hit.rank == 1 and hit.score > 0
    assert hit.status == "current" and hit.version == "2.0" and hit.mode == "bm25"


def test_deprecated_is_excluded_by_default_and_found_when_asked(index) -> None:
    query = "requests per minute rate limit"
    assert all(h.doc_id != DEPRECATED for h in search(query, k=10, index=index))
    assert any(h.doc_id == DEPRECATED for h in search(query, k=10, include_deprecated=True, index=index))


def test_doc_id_filter_limits_to_one_document(index) -> None:
    hits = search("timeout limit", k=5, doc_id="timeouts", index=index)
    assert hits and all(h.doc_id == "timeouts" for h in hits)


class FakeEmbed:
    """Deterministic bag-of-words vectors, so the hybrid path is tested offline."""

    def __call__(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                out[row, int(hashlib.md5(token.encode()).hexdigest(), 16) % 64] += 1.0
        return out


def test_hybrid_fuses_both_rankings_with_rrf(folder) -> None:
    idx = build_index(load_docs(folder), embed=FakeEmbed())
    assert idx.mode == "hybrid"
    query = "429 Too Many Requests ERR_RATE_LIMITED"
    hybrid = search(query, k=3, method="hybrid", index=idx)
    bm25 = {h.section_id: h.rank for h in search(query, k=10, method="bm25", index=idx)}
    dense = {h.section_id: h.rank for h in search(query, k=10, method="dense", index=idx)}
    top = hybrid[0]
    assert top.score == pytest.approx(1 / (60 + bm25[top.section_id]) + 1 / (60 + dense[top.section_id]), abs=1e-6)
    assert top.dense_score is not None and all(h.doc_id != DEPRECATED for h in hybrid)


def test_hybrid_without_embeddings_is_an_error(index) -> None:
    with pytest.raises(RuntimeError):
        search("x", method="hybrid", index=index)


def test_empty_index_returns_no_hits() -> None:
    idx = build_index([])
    assert search("anything", index=idx) == []


def test_first_sentences_quotes_the_start_of_a_section(index) -> None:
    section = get_section("doc:limits#1", index=index)
    quote = first_sentences(section.text, 1)
    assert quote == "Each key may send 100 requests per minute."
    assert first_sentences("- one\n- two", 2) == "one; two"


def test_get_index_is_built_once(monkeypatch, folder) -> None:
    doc_search.get_index.cache_clear()
    monkeypatch.setattr(doc_search, "load_docs", lambda: load_docs(folder))
    try:
        assert doc_search.get_index() is doc_search.get_index()
        assert len(doc_search.get_index().sections) == 4
    finally:
        doc_search.get_index.cache_clear()
