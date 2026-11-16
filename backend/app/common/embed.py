"""Text embeddings with a small pretrained model (fastembed, BAAI/bge-small-en-v1.5, 384 numbers, CPU).

The model downloads once (~65 MB) into backend/.cache/fastembed (git-ignored); after that it works offline.
Environment variables: EMBED_MODEL (model name), EMBED_CACHE_DIR (where model files go).
fastembed is imported only when a model is first needed, so tests with a fake embedder never load it.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from app.common.paths import CACHE_DIR

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MODEL_DIR = Path(os.getenv("EMBED_CACHE_DIR") or CACHE_DIR / "fastembed")


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> Any:
    from fastembed import TextEmbedding

    logger.info("loading embedding model %s", model_name)
    return TextEmbedding(model_name=model_name, cache_dir=str(MODEL_DIR))


def l2_normalise(m: np.ndarray) -> np.ndarray:
    """Scale each row to length 1; an all-zero row stays zero instead of becoming NaN."""
    m = np.asarray(m, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(norms == 0, 1.0, norms)


def embed_texts(texts: list[str], model_name: str = DEFAULT_MODEL, batch_size: int = 64) -> np.ndarray:
    """Embed texts; returns unit-length float32 rows, shape (n, dim)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vectors = np.array(list(_load_model(model_name).embed(texts, batch_size=batch_size)), dtype=np.float32)
    return l2_normalise(vectors)
