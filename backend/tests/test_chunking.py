"""KB chunking (app/tickets/chunking.py) and KB search (app/tickets/kb.py) on a tiny KB folder. BM25 only."""
from pathlib import Path

import pytest

from app.common.doc_search import build_index
from app.common.docs_loader import load_docs
from app.tickets import chunking, kb
from app.tickets.chunking import (contains_sentence, context_key, contextual_chunks, evaluate, fixed_chunks,
                                  heading_chunks, index_view, word_windows)
from app.tickets.kb import KBIndex, search_kb

ARTICLE = """---
version: "1.0"
status: current
updated: 2026-11-17
doc_id: {doc}
category: "Network"
service: "VPN"
---

# {title}

## Symptoms

{symptoms}

## Resolution steps

1. Reset the **VPN client** profile.
2. Reconnect using the new certificate.
"""


@pytest.fixture(scope="module")
def folder(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("docs")
    (root / "kb").mkdir()
    long_words = " ".join(f"w{i}" for i in range(250))  # makes the fixed windows overlap
    (root / "kb" / "KB-900.md").write_text(ARTICLE.format(doc="KB-900", title="VPN error 809",
                                                          symptoms="The VPN fails with error 809. " + long_words),
                                           encoding="utf-8")
    (root / "kb" / "KB-901.md").write_text(ARTICLE.format(doc="KB-901", title="Printer jams",
                                                          symptoms="Paper jams in the printer."), encoding="utf-8")
    (root / "other.md").write_text("# Not KB\n\n## A\n\nNot a KB article.", encoding="utf-8")
    return root


def test_heading_ids_match_docs_loader(folder):
    chunks = heading_chunks(folder)
    assert [c.id for c in chunks] == [s.id for s in load_docs(folder) if s.doc_id.startswith("kb/")]
    assert chunks[0].id == "doc:kb/KB-900#1" and all(c.doc_id.startswith("kb/") for c in chunks)


def test_fixed_windows_overlap():
    words = [f"w{i}" for i in range(300)]
    windows = word_windows(words, 120, 30)
    assert [w[0] for w in windows] == ["w0", "w90", "w180"] and windows[-1][-1] == "w299"
    for a, b in zip(windows, windows[1:]):
        assert a[-30:] == b[:30]  # 30 shared words: a sentence at the edge is whole in one of them
    assert word_windows(words[:50], 120, 30) == [words[:50]]
    with pytest.raises(ValueError):
        word_windows(words, 10, 10)


def test_fixed_chunks_ignore_headings_and_use_the_title(folder):
    chunks = [c for c in fixed_chunks(words=120, overlap=30, folder=folder) if c.doc_id == "kb/KB-900"]
    assert [c.id for c in chunks] == ["doc:kb/KB-900#f1", "doc:kb/KB-900#f2", "doc:kb/KB-900#f3"]
    assert {c.heading for c in chunks} == {"VPN error 809"}
    assert "Symptoms" not in " ".join(c.text for c in chunks)  # headings are not part of the windows


def test_kb_hit_text_never_contains_the_prefix(folder):
    prefix = "Zebra article about tunnels and quokka certificates."  # words found ONLY in the prefix
    context = {context_key(s): {"prefix": prefix} for s in heading_chunks(folder)}
    pairs = contextual_chunks(folder, context=context)
    indexed = index_view(pairs)
    assert all(s.text.startswith(prefix) for s in indexed)  # the index does see the prefix...
    index = KBIndex("contextual", build_index(indexed), {s.id: s for s, _ in pairs})
    hits = search_kb("zebra quokka", k=3, index=index)
    assert hits  # ...so a prefix-only query still finds chunks
    for hit in hits:
        assert prefix not in hit.text and "zebra" not in hit.text.lower()  # ...but never returns it
        assert hit.text == index.shown[hit.section_id].text


def test_a_changed_section_falls_back_instead_of_reusing_a_stale_prefix(folder):
    section = heading_chunks(folder)[0]
    stale = {f"{section.id}@0000000000000000": {"prefix": "Old prefix."}}
    (_, prefix), *_ = contextual_chunks(folder, context=stale)
    assert prefix == "VPN error 809 (Network, VPN): Symptoms."  # deterministic fallback from front matter


def test_kb_search_score_and_empty_index(folder):
    shown = {s.id: s for s in heading_chunks(folder)}
    index = KBIndex("heading", build_index(list(shown.values())), shown)
    hit = search_kb("reset the VPN client profile", k=1, index=index)[0]
    assert hit.section_id == "doc:kb/KB-900#2" and hit.score > 0  # BM25 score when embeddings are off
    assert search_kb("anything", index=KBIndex("heading", build_index([]), {})) == []
    assert kb.kb_strategy() in chunking.STRATEGIES


def test_eval_hit_logic_on_a_toy_case(folder):
    shown = {s.id: s for s in heading_chunks(folder)}
    assert contains_sentence("1. Reset the **VPN client**\nprofile.", "reset the VPN client profile.")
    assert not contains_sentence("Reset the VPN", "")
    queries = [{"query": "q1", "key_sentence": "Reconnect using the new certificate.", "gold_doc": "KB-900"},
               {"query": "q2", "key_sentence": "Paper jams in the printer.", "gold_doc": "KB-901"}]
    ranked = {"q1": ["doc:kb/KB-901#1", "doc:kb/KB-900#2"],   # key sentence at rank 2 (and gold doc in top 3)
              "q2": ["doc:kb/KB-900#1", "doc:kb/KB-900#2"]}   # never found, wrong article
    scores = evaluate(queries, lambda q, depth: ranked[q][:depth], shown, k=3)
    assert scores == {"recall_at_k": 0.5, "mrr": 0.25, "doc_recall_at_k": 0.5}
    assert evaluate(queries[:1], lambda q, depth: ranked[q][:depth], shown, k=1)["recall_at_k"] == 0.0
