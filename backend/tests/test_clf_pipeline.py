"""clf_pipeline (FR-01): the classifier's text, masking and TF-IDF. Small made-up rows, plus split counts of the
real tickets.csv (counts only; no row is printed)."""
import re

from sklearn.linear_model import LogisticRegression

from app.common.redact import mask
from app.tickets import clf_pipeline as cp
from app.tickets.features import LEAKY_COLUMNS, row_text

KEY = "AIza" + "DUMMYdummy" * 3 + "1234"   # fake, key-shaped (built at run time so scanners do not flag it)
GROQ = "gsk_" + "Q" * 30
OPENAI = "sk-proj-" + "Z" * 30


def _row(subject, description, category, **extra):
    base = {"ticket_id": "T1", "subject": subject, "description": description, "category": category,
            "channel": "email", "environment": "production", "affected_service": "VPN", "split": "train"}
    # Leaky columns get a token that must never reach the vocabulary.
    base.update({c: "leakytoken" for c in LEAKY_COLUMNS if c not in base})
    base.update(extra)
    return base


TRAIN = [
    _row("vpn will not connect", "tunnel drops from home", "Network"),
    _row("vpn gateway timeout", f"error after login, key {KEY}", "Network"),
    _row("outlook asks for password", f"mailbox prompt loops {GROQ}", "Email"),
    _row("mail stuck in outbox", f"send fails {OPENAI}", "Email"),
] * 3
VAL_ONLY_WORD = "zebracorn"


def _vocab(model):
    tfidf = model.named_steps["tfidf"]
    return {term for _, vec in tfidf.transformer_list for term in vec.vocabulary_}


def _fit(rows=TRAIN):
    return cp.make_pipeline(LogisticRegression(max_iter=500)).fit(rows, [r["category"] for r in rows])


def test_text_step_is_exactly_row_text_then_masked():
    model = _fit()
    rows = TRAIN[:4]
    out = model.named_steps["text"].transform(rows)
    assert out == [mask(row_text(r)) for r in rows]
    # Rows without secrets: the text step returns row_text unchanged (same recipe as features.py).
    assert model.named_steps["text"].transform(rows[:1]) == [row_text(rows[0])]


def test_vocab_comes_from_train_only():
    model = _fit()
    model.predict([_row("zebracorn printer", VAL_ONLY_WORD, "?")])
    assert not any(VAL_ONLY_WORD in t for t in _vocab(model))
    assert "vpn" in _vocab(model)


def test_no_leaky_column_reaches_the_features():
    assert not any("leakytoken" in t or "leaky" in t for t in _vocab(_fit()))
    for col in ("root_cause", "resolution_notes", "resolution_steps", "resolved_at", "status", "assigned_team",
                "problem_id", "dup_group_id", "split", "ticket_id"):
        assert col in cp.LEAKY_COLS


def test_no_key_like_string_in_vocab():
    key_like = re.compile(r"(aiza|gsk_|sk-)[a-z0-9_-]{8,}|dummydummy|qqqqqqqq|zzzzzzzz")
    assert not [t for t in _vocab(_fit()) if key_like.search(t)]


def test_two_fits_are_identical():
    a, b = _fit(), _fit()
    assert _vocab(a) == _vocab(b)
    assert (a.predict_proba(TRAIN) == b.predict_proba(TRAIN)).all()


def test_load_splits_uses_the_existing_split_column():
    counts = {k: len(v) for k, v in cp.load_splits().items()}
    assert counts == {"train": 770, "val": 176, "test": 154}


def test_load_splits_without_test_holds_no_test_row():
    data = cp.load_splits(("train", "val"))
    assert set(data) == {"train", "val"}
    assert all(r["split"] != "test" for rows in data.values() for r in rows)
