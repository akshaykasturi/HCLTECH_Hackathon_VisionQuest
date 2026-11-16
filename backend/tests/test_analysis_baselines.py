"""Evaluation baselines (app/analysis/baselines.py) on a tiny made-up dataset."""
import numpy as np
import pytest

from app.analysis.baselines import majority_baseline, text_only_baseline

TRAIN = ["disk is full on the server", "disk space low", "no space left on disk",
         "password expired for user", "login failed bad password", "password reset needed"] * 2
LABELS = ["storage", "storage", "storage", "access", "access", "access"] * 2
TEST = ["disk almost full", "user cannot login password wrong"]


def test_majority_baseline_returns_the_most_common_label() -> None:
    assert majority_baseline(["a", "b", "b"], 3) == ["b", "b", "b"]
    assert majority_baseline(["b", "a"], 1) == ["a"]  # tie: alphabetically first
    with pytest.raises(ValueError):
        majority_baseline([], 1)


def test_text_only_baseline_learns_from_words() -> None:
    result = text_only_baseline(TRAIN, LABELS, TEST, min_df_words=1, min_df_chars=1)
    assert list(result.predictions) == ["storage", "access"]
    assert result.accuracy(["storage", "access"]) == 1.0
    assert result.probabilities.shape == (2, 2) and result.classes == ["access", "storage"]
    assert np.allclose(result.probabilities.sum(axis=1), 1.0)


def test_vocabulary_comes_from_train_only() -> None:
    result = text_only_baseline(TRAIN, LABELS, ["zebra unicorn"], min_df_words=1, min_df_chars=1)
    words = result.model.named_steps["tfidf"].transformer_list[0][1].vocabulary_
    assert "zebra" not in words and "disk" in words


def test_same_input_gives_the_same_output() -> None:
    a = text_only_baseline(TRAIN, LABELS, TEST, min_df_words=1, min_df_chars=1)
    b = text_only_baseline(TRAIN, LABELS, TEST, min_df_words=1, min_df_chars=1)
    assert np.array_equal(a.probabilities, b.probabilities)


def test_bad_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        text_only_baseline(["a"], ["x", "y"], ["b"])
    with pytest.raises(ValueError):
        text_only_baseline(["a", "b"], ["x", "x"], ["c"])
