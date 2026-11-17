"""Three ways to cut the KB articles (data/docs/kb) into searchable chunks, for the chunking trial (mentor).

  heading     one chunk per "##" section, as docs_loader gives them (ids doc:kb/KB-020#2)
  fixed       each article's full text cut into word windows (default 120 words, 30 overlapping), headings
              ignored (ids doc:kb/KB-020#f1); a sentence cut at a window edge is still whole in the next window
  contextual  heading chunks + a 1-2 sentence context prefix (article title, category, service, what the
              section covers). The prefix is used ONLY to index: search sees it, nobody is ever shown it, and
              KBHit.text / citations stay the original section text.

Prefixes are generated once by scripts/build_kb_context.py and committed in backend/data/kb_context.json,
keyed by section id + a hash of the section text, so a changed section never reuses a stale prefix (it falls back
to the deterministic prefix until the script is run again). Tickets are not chunked (one ticket = one unit).

Also here: the evaluation of the trial (key-sentence and document hits), so tests can check the hit logic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import yaml

from app.common.docs_loader import DocSection, load_docs
from app.common.paths import DATA_DIR

logger = logging.getLogger(__name__)

DOCS_ROOT = DATA_DIR / "docs"
KB_PREFIX = "kb/"
CONTEXT_FILE = DATA_DIR / "kb_context.json"
STRATEGIES = ("fixed", "heading", "contextual")


# ---- Heading chunks -----------------------------------------------------------------------------

def heading_chunks(folder: Optional[Path] = None) -> list[DocSection]:
    """docs_loader's sections as they are, KB articles only (one per "##" section)."""
    return [s for s in load_docs(folder or DOCS_ROOT) if s.doc_id.startswith(KB_PREFIX)]


# ---- Fixed windows ------------------------------------------------------------------------------

def _by_doc(sections: Iterable[DocSection]) -> dict[str, list[DocSection]]:
    docs: dict[str, list[DocSection]] = {}
    for s in sections:
        docs.setdefault(s.doc_id, []).append(s)
    return docs


def word_windows(words: list[str], size: int, overlap: int) -> list[list[str]]:
    """Windows of `size` words, each starting size - overlap words after the previous one; the last window
    ends at the last word (no tiny tail window)."""
    if size <= 0 or not 0 <= overlap < size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    if len(words) <= size:
        return [words] if words else []
    step = size - overlap
    out = []
    for start in range(0, len(words), step):
        out.append(words[start:start + size])
        if start + size >= len(words):
            break
    return out


def fixed_chunks(words: int = 120, overlap: int = 30, folder: Optional[Path] = None) -> list[DocSection]:
    """Each article's full text (all sections, headings left out) cut into word windows."""
    out: list[DocSection] = []
    for doc_id, sections in _by_doc(heading_chunks(folder)).items():
        first = sections[0]
        all_words = " ".join(s.text for s in sections).split()
        title = first.title or first.heading_path[0]
        for n, window in enumerate(word_windows(all_words, words, overlap), start=1):
            out.append(first.model_copy(update={"id": f"doc:{doc_id}#f{n}", "heading": title,
                                                "heading_path": [title], "text": " ".join(window)}))
    return out


# ---- Contextual prefixes ------------------------------------------------------------------------

def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def context_key(section: DocSection) -> str:
    return f"{section.id}@{text_hash(section.text)}"


@lru_cache(maxsize=64)
def _front_matter(source_path: str) -> dict[str, Any]:
    """The article's front matter (category, service...): docs_loader does not keep these fields."""
    path = Path(source_path)
    if not path.is_absolute():
        path = DATA_DIR.parent / path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    try:
        return (yaml.safe_load(m.group(1)) or {}) if m else {}
    except yaml.YAMLError:
        return {}


def section_facts(section: DocSection) -> dict[str, str]:
    meta = _front_matter(section.source_path)
    return {"title": section.title or section.heading_path[0], "category": str(meta.get("category") or "IT"),
            "service": str(meta.get("service") or "general"), "heading": section.heading}


def fallback_prefix(section: DocSection) -> str:
    f = section_facts(section)
    return f"{f['title']} ({f['category']}, {f['service']}): {f['heading']}."


def load_context(path: Path = CONTEXT_FILE) -> dict[str, dict[str, str]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def contextual_chunks(folder: Optional[Path] = None, context: Optional[dict[str, dict[str, str]]] = None
                      ) -> list[tuple[DocSection, str]]:
    """(original heading chunk, prefix) pairs. Index prefix + text; show and cite the original text only."""
    context = load_context() if context is None else context
    out, missing = [], 0
    for s in heading_chunks(folder):
        entry = context.get(context_key(s))
        if entry is None:
            missing += 1
        out.append((s, entry["prefix"] if entry else fallback_prefix(s)))
    if missing:
        logger.warning("kb chunking: %d section(s) have no stored prefix; deterministic fallback used "
                       "(run scripts/build_kb_context.py)", missing)
    return out


def index_view(pairs: list[tuple[DocSection, str]]) -> list[DocSection]:
    """What the search index sees for contextual chunks: the prefix in front of the text, same id."""
    return [s.model_copy(update={"text": f"{prefix}\n{s.text}"}) for s, prefix in pairs]


def build_chunks(strategy: str, folder: Optional[Path] = None) -> tuple[list[DocSection], dict[str, DocSection]]:
    """(sections to index, {id: original section to show}) for a strategy."""
    if strategy == "heading":
        shown = heading_chunks(folder)
        return shown, {s.id: s for s in shown}
    if strategy == "fixed":
        shown = fixed_chunks(folder=folder)
        return shown, {s.id: s for s in shown}
    if strategy == "contextual":
        pairs = contextual_chunks(folder)
        return index_view(pairs), {s.id: s for s, _ in pairs}
    raise ValueError(f"unknown chunking strategy {strategy!r}; use one of {STRATEGIES}")


# ---- Evaluation of the trial ----------------------------------------------------------------------

_MARKUP = re.compile(r"[*`_>#]")


def normalise(text: str) -> str:
    """Lower case, markdown emphasis removed, one space between words (so line breaks don't matter)."""
    return " ".join(_MARKUP.sub(" ", text).lower().split())


def contains_sentence(chunk_text: str, sentence: str) -> bool:
    return bool(sentence.strip()) and normalise(sentence) in normalise(chunk_text)


def doc_key(doc_id: str) -> str:
    """'kb/KB-020' -> 'KB-020' (labelled cases name the article without the folder)."""
    return doc_id.rsplit("/", 1)[-1]


def evaluate(queries: list[dict[str, str]], search: Callable[[str, int], list[str]],
             shown: dict[str, DocSection], k: int = 3, depth: int = 10) -> dict[str, float]:
    """Score one strategy x method. `search(query, depth)` returns chunk ids best-first.
    A query is {"query", "key_sentence", "gold_doc"}. Hit = a chunk's ORIGINAL text contains the key sentence.
      recall_at_k      share of queries with a hit in the top k
      mrr              1 / rank of the first hit within `depth` (0 if none), averaged
      doc_recall_at_k  share of queries with a top-k chunk from the gold article"""
    hits = mrr = doc_hits = 0.0
    for q in queries:
        ids = search(q["query"], depth)
        rank = next((r for r, i in enumerate(ids, start=1) if contains_sentence(shown[i].text, q["key_sentence"])),
                    None)
        hits += rank is not None and rank <= k
        mrr += 1 / rank if rank else 0.0
        doc_hits += any(doc_key(shown[i].doc_id) == q["gold_doc"] for i in ids[:k])
    n = len(queries) or 1
    return {"recall_at_k": round(hits / n, 4), "mrr": round(mrr / n, 4), "doc_recall_at_k": round(doc_hits / n, 4)}
