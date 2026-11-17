"""What the ticket classifier reads (FR-01). One recipe, used by training AND by classify() at run time.

If training built the text one way and the live classifier another way, the model would see a different kind
of input in production than it learned from, and its accuracy would quietly drop. So both call ticket_text().
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin

from app.common.paths import DATA_DIR

# Change this string whenever ticket_text() changes: saved embeddings record it, so training can tell that
# features built with an older recipe are stale.
TEXT_RECIPE = "v1: subject + '. ' + description, whitespace collapsed"

TEXT_COLUMNS: tuple[str, ...] = ("subject", "description")   # the only columns the text is built from

LEAKY_COLUMNS: frozenset[str] = frozenset({
    "root_cause", "resolution_notes", "resolution_steps", "resolved_at", "status",
    "category", "subcategory", "priority", "assigned_team", "problem_id", "dup_group_id",
})
"""Columns that must never be used as features.

They are only known AFTER a ticket has been worked on: the root cause, the fix, when and by whom it was resolved,
its final status, its problem record and duplicate group. category / subcategory / priority are the labels the
classifier predicts. A new ticket arriving at the assistant has none of these, so a model that learned from them
would look excellent in evaluation and fail on real tickets (target leakage).
"""

_SPACE = re.compile(r"\s+")


def ticket_text(subject: Optional[str], description: Optional[str]) -> str:
    """subject + ". " + description, with every run of whitespace (spaces, tabs, newlines) collapsed to one space.
    A missing part is left out rather than leaving a stray ". "."""
    s = _SPACE.sub(" ", subject or "").strip()
    d = _SPACE.sub(" ", description or "").strip()
    return f"{s}. {d}" if s and d else s or d


def row_text(row: Mapping[str, Any]) -> str:
    """ticket_text() for a tickets.csv row (or any mapping with the data's own field names). Reads only
    TEXT_COLUMNS, so leaky columns in the row can never reach the text."""
    return ticket_text(row.get("subject"), row.get("description"))


assert not LEAKY_COLUMNS & set(TEXT_COLUMNS), "a text column is listed as leaky"


EMBEDDINGS_NPZ = DATA_DIR / "processed" / "ticket_embeddings.npz"
TICKETS_CSV = DATA_DIR / "tickets" / "tickets.csv"


class StaleEmbeddingsError(RuntimeError):
    """Saved embeddings are missing, or were built from another tickets.csv or text recipe."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_embedding_cache(splits: Sequence[str] = ("train", "val"), csv_path: Path = TICKETS_CSV,
                         npz_path: Path = EMBEDDINGS_NPZ) -> dict[str, np.ndarray]:
    """{ticket_id: vector} for the requested splits only (test is left out unless asked for). Raises
    StaleEmbeddingsError (message ends with what to do) if the file is missing or doesn't match tickets.csv."""
    meta_path = Path(npz_path).with_name(Path(npz_path).stem + ".meta.json")
    if not Path(npz_path).exists() or not meta_path.exists():
        raise StaleEmbeddingsError("embeddings not found: rerun scripts/embed_tickets.py")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("tickets_csv_sha256") != _sha256(csv_path):
        raise StaleEmbeddingsError("embeddings were built from a different tickets.csv: rerun scripts/embed_tickets.py")
    if meta.get("text_recipe") != TEXT_RECIPE:
        raise StaleEmbeddingsError("embeddings use an older text recipe: rerun scripts/embed_tickets.py")
    data = np.load(npz_path)
    wanted = set(splits)
    return {str(t): np.asarray(v, dtype=np.float64)
            for t, s, v in zip(data["ticket_id"], data["split"], data["vectors"]) if str(s) in wanted}


class EmbedRows(BaseEstimator, TransformerMixin):
    """Pipeline step: rows (dicts with the data's own field names) -> embedding vectors of row_text(row).

    The embedding-based twin of clf_pipeline.make_pipeline's text step, so embedding and TF-IDF models take the
    same input and are scored by the same code. `cache` maps ticket_id -> vector (from
    data/processed/ticket_embeddings.npz) so training does not re-embed; rows not in it (a live ticket) are
    embedded with app.common.embed.embed_texts, in memory only. Set cache=None before saving the model."""

    def __init__(self, cache: Optional[Mapping[str, Any]] = None):
        self.cache = cache

    def fit(self, rows: Sequence[Mapping[str, Any]], y: Any = None) -> "EmbedRows":
        return self

    def transform(self, rows: Sequence[Mapping[str, Any]]) -> np.ndarray:
        rows = list(rows)
        cache = self.cache or {}
        out: list[Optional[np.ndarray]] = [cache.get(str(r.get("ticket_id"))) if r.get("ticket_id") else None
                                           for r in rows]
        missing = [i for i, v in enumerate(out) if v is None]
        if missing:
            from app.common.embed import embed_texts  # loads the model only when a row is not cached
            fresh = embed_texts([row_text(rows[i]) for i in missing])
            for i, v in zip(missing, fresh):
                out[i] = v
        return np.asarray(np.vstack(out) if out else np.zeros((0, 0)), dtype=np.float64)
