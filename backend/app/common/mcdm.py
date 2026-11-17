"""Multi-criteria ranking maths: entropy weights, blended weights, TOPSIS. Pure numpy, no ticket code.

Words used below:
- X: one row per candidate, one column per criterion (any non-negative scale; TOPSIS normalises it).
- benefit[j]: True if higher is better for criterion j, False for a cost criterion (lower is better).
- Entropy weight: how much a criterion actually separates THESE candidates. A column where every candidate
  has the same value says nothing and gets weight 0.
- Prior weight: how important people think a criterion is (from policy.yaml).
- TOPSIS closeness C in [0, 1]: distance to the worst candidate profile / (that + distance to the best one).
  It is RELATIVE: the best of a bad set still scores near 1. Never use it as "how good is the evidence";
  the gate keeps using the absolute retrieval score for that.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)


def _matrix(X) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    if X.ndim == 1:
        X = X.reshape(1, -1) if X.size else X.reshape(0, 0)
    if X.ndim != 2:
        raise ValueError(f"X must be candidates x criteria (2-D), got shape {X.shape}")
    if np.isnan(X).any() or np.isinf(X).any():
        raise ValueError("X contains NaN or infinite values; drop the criterion before ranking")
    return X


def _benefit(benefit: Optional[Sequence[bool]], n: int) -> np.ndarray:
    if benefit is None:
        return np.ones(n, dtype=bool)
    b = np.asarray(benefit, dtype=bool).reshape(-1)
    if b.size != n:
        raise ValueError(f"benefit has {b.size} entries, X has {n} criteria")
    return b


# ---- Entropy weights --------------------------------------------------------------------------------

def entropy_weights(X, benefit: Optional[Sequence[bool]] = None, eps: float = 1e-12) -> Optional[np.ndarray]:
    """Objective weights from how much each criterion varies across the candidates. Sums to 1.

    Per column j:
      y_ij = (x_ij - min) / (max - min)   benefit       (min-max, direction-aware, so a cost column and
      y_ij = (max - x_ij) / (max - min)   cost           its mirror image get the same weight)
      p_ij = y_ij / sum_i y_ij
      e_j  = -sum_i p_ij ln p_ij / ln m   (0 ln 0 = 0)   1 = spread evenly, 0 = one candidate holds it all
      d_j  = 1 - e_j;  w_j = d_j / sum_k d_k
    Returns None when m < 2 (no spread to measure; the caller uses prior weights). A constant column gets
    d_j = 0; if every column is constant, equal weights. NaN raises ValueError.
    """
    X = _matrix(X)
    m, n = X.shape
    if m < 2:
        return None
    b = _benefit(benefit, n)
    lo, hi = X.min(axis=0), X.max(axis=0)
    spread = hi - lo
    varies = spread > eps * np.maximum(1.0, np.abs(hi))

    d = np.zeros(n)
    if varies.any():
        Xv, lo_v, hi_v, s = X[:, varies], lo[varies], hi[varies], spread[varies]
        Y = np.where(b[varies], (Xv - lo_v) / s, (hi_v - Xv) / s)  # each column now spans exactly [0, 1]
        P = Y / Y.sum(axis=0)  # column sums are >= 1, never 0
        plogp = np.where(P > 0, P * np.log(np.where(P > 0, P, 1.0)), 0.0)
        e = -plogp.sum(axis=0) / np.log(m)
        d[varies] = np.clip(1.0 - e, 0.0, None)

    if d.sum() <= eps:
        return np.full(n, 1.0 / n)
    return d / d.sum()


# ---- Blend with prior weights -----------------------------------------------------------------------

def _apply_floor(w: np.ndarray, floor: float) -> np.ndarray:
    """Weights summing to 1 with every weight >= floor: floored ones are fixed at floor, the rest share
    what is left in proportion (repeated until no weight falls below the floor)."""
    w = w / w.sum()
    fixed = np.zeros(w.size, dtype=bool)
    for _ in range(w.size):
        low = ~fixed & (w < floor)
        if not low.any():
            break
        fixed |= low
        free = ~fixed
        rest = 1.0 - floor * fixed.sum()
        w = np.where(fixed, floor, w)
        if free.any():
            total = w[free].sum()
            w[free] = w[free] / total * rest if total > 0 else rest / free.sum()
    return w


def blend_weights(w_entropy: Optional[Sequence[float]], w_prior: Sequence[float], lam: float,
                  floor: float) -> np.ndarray:
    """w = lam * w_entropy + (1 - lam) * w_prior, then every weight >= floor, summing to 1.
    w_entropy None (too few candidates) -> the prior alone (still floored)."""
    p = np.asarray(w_prior, dtype=float).reshape(-1)
    n = p.size
    if n == 0:
        return p
    if not 0.0 <= lam <= 1.0:
        raise ValueError(f"lam must be in [0, 1], got {lam}")
    if floor < 0 or floor * n >= 1.0:
        raise ValueError(f"weight_floor {floor} is impossible for {n} criteria (need 0 <= floor * n < 1)")
    if not np.isfinite(p).all() or (p < 0).any():
        raise ValueError("prior weights must be finite and >= 0")
    p = p / p.sum() if p.sum() > 0 else np.full(n, 1.0 / n)
    if w_entropy is None:
        w = p
    else:
        e = np.asarray(w_entropy, dtype=float).reshape(-1)
        if e.size != n:
            raise ValueError(f"w_entropy has {e.size} entries, w_prior has {n}")
        w = lam * e + (1.0 - lam) * p
    if w.sum() <= 0:
        w = np.full(n, 1.0 / n)
    return _apply_floor(w, floor)


# ---- TOPSIS -------------------------------------------------------------------------------------------

@dataclass
class TopsisResult:
    closeness: np.ndarray       # (m,) in [0, 1], higher = better
    d_plus: np.ndarray          # (m,) distance to the best profile
    d_minus: np.ndarray         # (m,) distance to the worst profile
    contributions: np.ndarray   # (m, n) share of each criterion in d_minus^2; rows sum to 1 (or are all 0)


def topsis(X, w: Sequence[float], benefit: Optional[Sequence[bool]] = None) -> TopsisResult:
    """Classic TOPSIS.

      r_ij = x_ij / sqrt(sum_i x_ij^2)    vector normalisation (an all-zero column stays 0)
      v_ij = w_j * r_ij
      best_j  = max_i v_ij (benefit) or min_i v_ij (cost);  worst_j = the other one
      d+_i = ||v_i - best||,  d-_i = ||v_i - worst||,  C_i = d-_i / (d+_i + d-_i)   (0.5 if both are 0)
    contributions[i, j] = (v_ij - worst_j)^2 / sum_j (v_ij - worst_j)^2: how much of candidate i's distance
    from the worst profile comes from criterion j (what pushed it up), for the "why ranked here" display.
    """
    X = _matrix(X)
    m, n = X.shape
    b = _benefit(benefit, n)
    w = np.asarray(w, dtype=float).reshape(-1)
    if w.size != n:
        raise ValueError(f"w has {w.size} entries, X has {n} criteria")
    if m == 0:
        empty = np.zeros(0)
        return TopsisResult(empty, empty, empty, np.zeros((0, n)))

    norms = np.sqrt((X ** 2).sum(axis=0))
    R = np.divide(X, norms, out=np.zeros_like(X), where=norms > 0)
    V = R * w
    best = np.where(b, V.max(axis=0), V.min(axis=0))
    worst = np.where(b, V.min(axis=0), V.max(axis=0))
    d_plus = np.sqrt(((V - best) ** 2).sum(axis=1))
    d_minus = np.sqrt(((V - worst) ** 2).sum(axis=1))
    total = d_plus + d_minus
    closeness = np.divide(d_minus, total, out=np.full(m, 0.5), where=total > 0)

    sq = (V - worst) ** 2
    row = sq.sum(axis=1, keepdims=True)
    contributions = np.divide(sq, row, out=np.zeros_like(sq), where=row > 0)
    return TopsisResult(np.clip(closeness, 0.0, 1.0), d_plus, d_minus, contributions)


def order(closeness: Sequence[float], tie_rank: Optional[Sequence[int]] = None) -> np.ndarray:
    """Indices best first. Ties (to 12 decimals) keep the original order (tie_rank, default row order)."""
    c = np.round(np.asarray(closeness, dtype=float), 12)
    tie = np.arange(c.size) if tie_rank is None else np.asarray(tie_rank)
    return np.lexsort((tie, -c))  # last key is the primary one
