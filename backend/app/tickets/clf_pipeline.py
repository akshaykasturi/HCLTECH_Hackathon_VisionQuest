"""The ticket classifier pipeline (FR-01): rows in, TF-IDF features, any scikit-learn classifier on top.

  text      app.tickets.features.row_text (Raswanth's single recipe: subject + description). Not redefined here,
            so training and classify() can never build the text two different ways.
  masking   app.common.redact.mask, in memory, inside the pipeline: a key or password in a ticket never reaches
            the vocabulary, and the saved model masks live tickets the same way.
  features  app.analysis.baselines.text_tfidf (word 1-2 grams + char 3-5 grams). Fitted inside the pipeline,
            so it learns its vocabulary from the training rows only.

classify() should load backend/models/clf_<label>.joblib and call predict_proba on rows (dicts with the data's own
field names); the pipeline applies row_text itself. backend/models/clf_manifest.json records TEXT_RECIPE: if it
differs from features.TEXT_RECIPE, the saved models are stale and must be retrained.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer

from app.analysis.baselines import text_tfidf
from app.common.paths import BACKEND_DIR, DATA_DIR
from app.common.redact import mask
from app.tickets.features import LEAKY_COLUMNS, TEXT_COLUMNS, TEXT_RECIPE, row_text

CSV_PATH = DATA_DIR / "tickets" / "tickets.csv"
MODELS_DIR = BACKEND_DIR / "models"
SPLITS = ("train", "val", "test")
LABELS = ("category", "subcategory", "priority")

# features.LEAKY_COLUMNS plus the bookkeeping columns that identify a row rather than describe the problem.
LEAKY_COLS: frozenset[str] = LEAKY_COLUMNS | {"split", "ticket_id"}
assert not LEAKY_COLS & set(TEXT_COLUMNS), "a text column is listed as leaky"

__all__ = ["CSV_PATH", "LABELS", "LEAKY_COLS", "MODELS_DIR", "TEXT_RECIPE", "load_splits", "make_pipeline",
           "rows_to_text"]


def load_splits(splits: Iterable[str] = SPLITS, csv_path: Path = CSV_PATH) -> dict[str, list[dict[str, Any]]]:
    """Rows of tickets.csv grouped by its own `split` column (already split by scenario; never re-split here).
    Only the requested splits are kept, so a caller that does not ask for "test" never holds a test row.
    Read-only: the CSV is never written."""
    wanted = tuple(splits)
    unknown = set(wanted) - set(SPLITS)
    if unknown:
        raise ValueError(f"unknown split(s) {sorted(unknown)}; choose from {SPLITS}")
    out: dict[str, list[dict[str, Any]]] = {s: [] for s in wanted}
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("split") in out:
                out[row["split"]].append(row)
    return out


def rows_to_text(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """row_text(row) with secrets masked, for each row. Module-level so the saved pipeline can be loaded again."""
    return [mask(row_text(r)) for r in rows]


def make_pipeline(estimator: Any) -> Pipeline:
    """Unfitted: rows -> masked text -> TF-IDF (fitted on whatever rows fit() gets) -> estimator."""
    return Pipeline([
        ("text", FunctionTransformer(rows_to_text, validate=False)),
        ("tfidf", text_tfidf()),
        ("clf", estimator),
    ])
