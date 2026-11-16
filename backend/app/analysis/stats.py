"""Statistics for the evaluation report: Brier score, bootstrap intervals, McNemar test, risk-coverage.

Pure functions: lists or numpy arrays in, floats or dicts out. numpy and the standard library only.
"""
import math

import numpy as np


def _as_arrays(confidence, correct) -> tuple[np.ndarray, np.ndarray]:
    """Turn two equal-length inputs into float and bool arrays, or raise ValueError."""
    conf = np.asarray(confidence, dtype=float)
    ok = np.asarray(correct, dtype=bool)
    if conf.shape != ok.shape or conf.ndim != 1:
        raise ValueError("confidence and correct must be 1-D and the same length")
    if conf.size == 0:
        raise ValueError("need at least one value")
    return conf, ok


def brier(confidence, correct) -> float:
    """Top-label Brier score: mean of (confidence - 1 if right else 0) squared; 0 is perfect.

    The app stores only the probability of its top answer, so this scores that answer
    (was the model right, and how sure was it), not the full one-vs-rest probability vector.
    """
    conf, ok = _as_arrays(confidence, correct)
    return float(np.mean((conf - ok.astype(float)) ** 2))


def bootstrap_ci(values, n: int = 2000, alpha: float = 0.05, seed: int = 0) -> tuple[float, float, float]:
    """Mean of values with a percentile bootstrap interval: (point, low, high)."""
    vals = np.asarray(values, dtype=float)
    if vals.ndim != 1 or vals.size == 0:
        raise ValueError("values must be a non-empty 1-D list")
    rng = np.random.default_rng(seed)
    # Each row is one resample: pick len(vals) items with replacement, then take its mean.
    idx = rng.integers(0, vals.size, size=(n, vals.size))
    means = vals[idx].mean(axis=1)
    low, high = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(vals.mean()), float(low), float(high)


def mcnemar(a_correct, b_correct) -> dict:
    """McNemar test on paired results of models A and B over the same items.

    b = A right and B wrong; c = A wrong and B right. Exact two-sided binomial test when
    b + c < 25, otherwise chi-square with continuity correction (1 degree of freedom).
    """
    a = np.asarray(a_correct, dtype=bool)
    m = np.asarray(b_correct, dtype=bool)
    if a.shape != m.shape or a.ndim != 1:
        raise ValueError("a_correct and b_correct must be 1-D and the same length")
    b = int(np.sum(a & ~m))
    c = int(np.sum(~a & m))
    n = b + c
    if n == 0:
        return {"b": 0, "c": 0, "n": 0, "statistic": 0.0, "p_value": 1.0, "method": "no_disagreements"}
    if n < 25:
        # Under "no difference", each disagreement is a fair coin flip: P(k or fewer of the rarer side), doubled.
        tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
        return {"b": b, "c": c, "n": n, "statistic": float(min(b, c)),
                "p_value": min(1.0, 2 * tail), "method": "exact_binomial"}
    stat = (abs(b - c) - 1) ** 2 / n
    # Chi-square with 1 degree of freedom: P(X > stat) = erfc(sqrt(stat / 2)).
    return {"b": b, "c": c, "n": n, "statistic": float(stat),
            "p_value": float(math.erfc(math.sqrt(stat / 2))), "method": "chi2_continuity"}


def risk_coverage(confidence, correct) -> list[dict]:
    """Accuracy against share answered: one row per distinct confidence used as the cut-off, high to low."""
    conf, ok = _as_arrays(confidence, correct)
    total = conf.size
    rows = []
    for t in np.unique(conf)[::-1]:
        answered = conf >= t
        k = int(answered.sum())
        acc = float(ok[answered].mean())
        rows.append({"threshold": float(t), "answered": k, "coverage": k / total,
                     "accuracy": acc, "risk": 1 - acc})
    return rows
