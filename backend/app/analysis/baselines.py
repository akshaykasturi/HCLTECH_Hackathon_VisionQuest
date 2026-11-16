"""Simple baselines for the evaluation report. For comparison only: the app never uses them.

A model is only worth reporting if it beats these:
  - majority_baseline: always say the most common label (the "do nothing smart" floor);
  - text_only_baseline: TF-IDF on the text alone + logistic regression (what words alone get you).

Fit on the TRAIN split only, tune on VALIDATION, report on TEST.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline


def majority_baseline(y_train: Sequence, n: int) -> list:
    """Predict the most common training label n times (ties: alphabetically first)."""
    counts = Counter(y_train)
    if not counts:
        raise ValueError("y_train is empty")
    best = max(counts.values())
    label = min(k for k, v in counts.items() if v == best)
    return [label] * n


def text_tfidf(min_df_words: int = 2, min_df_chars: int = 3) -> FeatureUnion:
    """Unfitted TF-IDF: word 1-2 grams + character 3-5 grams (catches codes and typos)."""
    return FeatureUnion([
        ("words", TfidfVectorizer(ngram_range=(1, 2), min_df=min_df_words, sublinear_tf=True)),
        ("chars", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=min_df_chars, sublinear_tf=True)),
    ])


@dataclass
class BaselineResult:
    predictions: np.ndarray
    probabilities: np.ndarray  # shape (rows, len(classes))
    classes: list[str]
    model: Pipeline

    def accuracy(self, y_true: Sequence) -> float:
        return float(np.mean(self.predictions == np.asarray(y_true, dtype=object)))


def text_only_baseline(train_texts: Sequence[str], train_labels: Sequence, test_texts: Sequence[str],
                       min_df_words: int = 2, min_df_chars: int = 3) -> BaselineResult:
    """TF-IDF + LogisticRegression fitted on the train texts only; predicts the test texts."""
    if len(train_texts) != len(train_labels):
        raise ValueError("train_texts and train_labels must be the same length")
    if len(set(train_labels)) < 2:
        raise ValueError("need at least two different labels to train")
    model = Pipeline([("tfidf", text_tfidf(min_df_words, min_df_chars)),
                      ("clf", LogisticRegression(max_iter=2000))])
    model.fit(list(train_texts), list(train_labels))
    texts = list(test_texts)
    return BaselineResult(predictions=model.predict(texts), probabilities=model.predict_proba(texts),
                          classes=[str(c) for c in model.classes_], model=model)
