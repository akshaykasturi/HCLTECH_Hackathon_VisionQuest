"""Embed every ticket in data/tickets/tickets.csv once, for the classifier (FR-01).

  text      app.tickets.features.ticket_text(subject, description): the same recipe classify() uses
  vectors   app.common.embed.embed_texts (BAAI/bge-small-en-v1.5, 384 numbers per ticket, length 1)
  output    data/processed/ticket_embeddings.npz       vectors (float32, n x dim), ticket_id, split
            data/processed/ticket_embeddings.meta.json model, dim, rows, text recipe, created time,
                                                       sha256 of tickets.csv (so training can spot stale files)
  checks    row count = CSV rows, unique ticket_id, no NaN, every row length ~1. Any failure stops the
            script before anything is written.
  sanity    1-nearest-neighbour on val: for each val ticket, the closest train ticket by cosine. How often do
            their categories match? Test is never used here.

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\embed_tickets.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.common.paths import DATA_DIR  # noqa: E402
from app.tickets.features import TEXT_RECIPE, row_text  # noqa: E402

logger = logging.getLogger("embed_tickets")

CSV_PATH = DATA_DIR / "tickets" / "tickets.csv"
OUT_DIR = DATA_DIR / "processed"
OUT_NAME = "ticket_embeddings"
BATCH_SIZE = 64
NORM_TOLERANCE = 1e-3

Embed = Callable[[list[str]], np.ndarray]


class EmbeddingCheckError(ValueError):
    """The embeddings are not safe to train on; the message says why."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def check(vectors: np.ndarray, ticket_ids: list[str], expected_rows: int) -> None:
    """Raise EmbeddingCheckError with a clear message if the vectors are not usable."""
    if vectors.ndim != 2 or vectors.shape[0] != expected_rows:
        raise EmbeddingCheckError(f"expected {expected_rows} rows (one per CSV row), got shape {vectors.shape}")
    if len(ticket_ids) != expected_rows:
        raise EmbeddingCheckError(f"expected {expected_rows} ticket_ids, got {len(ticket_ids)}")
    dupes = [t for t, n in Counter(ticket_ids).items() if n > 1]
    if dupes:
        raise EmbeddingCheckError(f"{len(dupes)} duplicate ticket_id(s), e.g. {dupes[:3]}")
    if not np.isfinite(vectors).all():
        bad = int((~np.isfinite(vectors)).any(axis=1).sum())
        raise EmbeddingCheckError(f"{bad} row(s) contain NaN or infinite values")
    norms = np.linalg.norm(vectors, axis=1)
    off = np.abs(norms - 1.0) > NORM_TOLERANCE
    if off.any():
        raise EmbeddingCheckError(f"{int(off.sum())} row(s) are not unit length (norms from {norms.min():.4f} "
                                  f"to {norms.max():.4f}); an empty text gives a zero vector")


def embed_rows(rows: list[dict[str, Any]], embed: Optional[Embed] = None,
               batch_size: int = BATCH_SIZE) -> tuple[np.ndarray, float]:
    """(vectors, seconds). `embed` overrides the real model (tests pass a fake)."""
    texts = [row_text(r) for r in rows]
    if embed is None:
        from app.common.embed import embed_texts
        embed = lambda t: embed_texts(t, batch_size=batch_size)  # noqa: E731
    start = time.perf_counter()
    vectors = np.asarray(embed(texts), dtype=np.float32)
    return vectors, time.perf_counter() - start


def save(out_dir: Path, vectors: np.ndarray, ticket_ids: list[str], splits: list[str],
         meta: dict[str, Any]) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    npz = out_dir / f"{OUT_NAME}.npz"
    meta_path = out_dir / f"{OUT_NAME}.meta.json"
    np.savez(npz, vectors=vectors.astype(np.float32), ticket_id=np.array(ticket_ids, dtype=str),
             split=np.array(splits, dtype=str))
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return npz, meta_path


def nn_accuracy(vectors: np.ndarray, splits: list[str], categories: list[str]) -> Optional[float]:
    """Share of val tickets whose nearest train ticket (cosine) has the same category. Test rows are never read."""
    s = np.array(splits)
    train, val = np.where(s == "train")[0], np.where(s == "val")[0]
    if not len(train) or not len(val):
        return None
    sims = vectors[val] @ vectors[train].T          # unit rows, so the dot product is the cosine
    nearest = train[sims.argmax(axis=1)]
    cats = np.array(categories)
    return float((cats[val] == cats[nearest]).mean())


def run(csv_path: Path = CSV_PATH, out_dir: Path = OUT_DIR, embed: Optional[Embed] = None,
        model_name: Optional[str] = None, batch_size: int = BATCH_SIZE) -> dict[str, Any]:
    """Embed, check, save. Returns a summary. Raises EmbeddingCheckError (nothing saved) on a failed check."""
    rows = read_rows(csv_path)
    if not rows:
        raise EmbeddingCheckError(f"{csv_path} has no rows")
    ticket_ids = [str(r.get("ticket_id") or "").strip() for r in rows]
    splits = [str(r.get("split") or "").strip() for r in rows]

    vectors, seconds = embed_rows(rows, embed, batch_size)
    check(vectors, ticket_ids, len(rows))
    if model_name is None:
        from app.common.embed import DEFAULT_MODEL
        model_name = DEFAULT_MODEL if embed is None else "custom"
    meta = {"model": model_name, "dim": int(vectors.shape[1]), "rows": len(rows), "text_recipe": TEXT_RECIPE,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tickets_csv": csv_path.name, "tickets_csv_sha256": sha256_of(csv_path),
            "splits": dict(Counter(splits)), "batch_size": batch_size, "seconds": round(seconds, 2)}
    npz, meta_path = save(out_dir, vectors, ticket_ids, splits, meta)
    logger.info("embedded %d tickets in %.1f s", len(rows), seconds)
    return {"npz": npz, "meta": meta_path, "seconds": seconds, "splits": meta["splits"], "dim": meta["dim"],
            "val_1nn_category_accuracy": nn_accuracy(vectors, splits, [r.get("category") or "" for r in rows])}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Embed tickets.csv for the classifier.")
    parser.add_argument("--csv", type=Path, default=CSV_PATH)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if not args.csv.exists():
        print(f"{args.csv} not found. Nothing to embed.")
        return 1
    try:
        res = run(args.csv, args.out_dir)
    except EmbeddingCheckError as exc:
        print(f"STOPPED, nothing saved: {exc}")
        return 1
    except Exception as exc:  # e.g. the model download failed
        print(f"STOPPED, nothing saved: embedding failed: {type(exc).__name__}: {exc}")
        return 1

    print(f"Embedded {sum(res['splits'].values())} tickets ({res['dim']} numbers each) in {res['seconds']:.1f} s")
    for split, n in sorted(res["splits"].items()):
        print(f"  {split:6s} {n}")
    acc = res["val_1nn_category_accuracy"]
    print(f"Val 1-nearest-neighbour category accuracy (nearest train ticket): "
          f"{'n/a' if acc is None else f'{acc:.3f}'}")
    print(f"Saved {res['npz']} and {res['meta'].name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
