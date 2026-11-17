"""classify.py (FR-01) on a tiny made-up dataset, offline: TF-IDF models and a fake embedder, no model download."""
import csv
import hashlib
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

import app.common.embed as embed_module
from app.common.doc_search import tokenize
from app.common.embed import l2_normalise
from app.tickets import classify as C
from app.tickets.models import TicketIn

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import compare_classifiers as akh  # noqa: E402  (Akhilesh's script)

WORDS = {  # category -> subcategory -> words
    "Network": {"VPN": "vpn tunnel error 809 connect", "WiFi": "wifi wireless signal access point"},
    "Email": {"Mailbox": "mailbox quota full outlook", "Spam": "spam junk phishing mail filter"},
    "Access": {"Password": "password reset expired login", "MFA": "mfa authenticator code token"},
}


def _rows():
    rows = []
    for cat, subs in WORDS.items():
        for sub, words in subs.items():
            for i in range(9):
                rows.append({"ticket_id": f"{sub}-{i}", "split": "train", "category": cat, "subcategory": sub,
                             "priority": "P3", "root_cause": f"{sub}-rc{i % 3}", "subject": f"{words} {i}",
                             "description": f"user reports {words}"})
    return rows


class FakeEmbed:
    def __call__(self, texts, *a, **k):
        out = np.zeros((len(texts), 32), dtype=np.float32)
        for row, text in enumerate(texts):
            for t in tokenize(text):
                out[row, int(hashlib.md5(t.encode()).hexdigest(), 16) % 32] += 1.0
        return l2_normalise(out)


SVM = {"kind": "tfidf", "name": "svm_C1", "params": {"C": 1, "class_weight": "balanced"}}
EMB = {"kind": "embedding", "name": "emb_lr", "params": {"C": 0.1}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    csv_path = tmp_path / "tickets.csv"
    rows = _rows()
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    choice = {"labels": {"category": {"use": "model", "primary": SVM, "fallback": SVM},
                         "subcategory": {"use": "model", "primary": EMB, "fallback": SVM},
                         "priority": {"use": "rule"}}}
    choice_path = tmp_path / "choice.json"
    choice_path.write_text(json.dumps(choice), encoding="utf-8")
    monkeypatch.setattr(C, "_STATE", C._State())
    monkeypatch.setattr(embed_module, "embed_texts", FakeEmbed())
    monkeypatch.setenv("DOC_SEARCH_DENSE", "1")   # conftest turns it off; these tests use the fake embedder
    return {"csv": csv_path, "choice": choice_path, "models": tmp_path / "models"}


def _load(env, **kw):
    return C.load_classifier(env["choice"], env["csv"], env["models"], **kw)


TICKET = TicketIn(subject="VPN tunnel error 809", description="cannot connect to vpn tunnel")


# ---- the app builds exactly what was compared --------------------------------------------------------

def test_tfidf_models_are_built_exactly_like_akhileshs_candidates():
    for cand in akh.candidates():
        mine, scale = C.build_estimator({"kind": "tfidf", "name": cand.name, "params": cand.params})
        assert type(mine) is type(cand.build()) and not scale
        assert mine.get_params() == cand.build().get_params(), cand.name


def test_calibration_folds_match_akhileshs():
    rows = _rows()
    y = [r["category"] for r in rows]
    mine, his = C.calibration_folds(rows, y), akh.group_folds(rows, y)
    assert all((a[0] == b[0]).all() and (a[1] == b[1]).all() for a, b in zip(mine, his))


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError):
        C.build_estimator({"kind": "tfidf", "name": "xgb", "params": {}})


# ---- classify -------------------------------------------------------------------------------------------

def test_classify_returns_a_valid_classification(env):
    assert _load(env) == {"category": "tfidf/svm_C1", "subcategory": "embedding/emb_lr"}
    c = C.classify(TICKET)
    assert c.category.label == "Network" and 0 < c.category.prob <= 1
    assert c.subcategory.label in WORDS[c.category.label]            # valid pair
    probs = [s.prob for s in c.top3_category]
    assert len(c.top3_category) == 3 and probs == sorted(probs, reverse=True)
    assert c.top3_category[0].label == c.category.label
    assert c.priority.label == "P3" and c.priority.prob == 0.0 and c.priority_rule == "DEFAULT_P3"
    assert "svm_C1" in c.model_version and "emb_lr" in c.model_version


def test_subcategory_is_always_a_valid_pair(env):
    _load(env)
    for subj in ("mailbox quota full", "mfa code not arriving", "wifi signal weak", "spam filter phishing"):
        c = C.classify(TicketIn(subject=subj, description=subj))
        assert c.subcategory.label in WORDS[c.category.label]


def test_falls_back_to_tfidf_when_embeddings_cannot_load(env, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("model download failed")
    monkeypatch.setattr(embed_module, "embed_texts", broken)
    assert _load(env)["subcategory"] == "tfidf/svm_C1"
    assert C.classify(TICKET).subcategory.label == "VPN"


def test_embeddings_switched_off_uses_the_fallback(env, monkeypatch):
    monkeypatch.setenv("DOC_SEARCH_DENSE", "0")
    monkeypatch.setattr(embed_module, "embed_texts", lambda *a, **k: pytest.fail("embeddings are off"))
    assert _load(env)["subcategory"] == "tfidf/svm_C1"


def test_switches_to_fallback_if_embeddings_fail_later(env, monkeypatch):
    _load(env)
    def broken(*a, **k):
        raise RuntimeError("out of memory")
    monkeypatch.setattr(embed_module, "embed_texts", broken)
    c = C.classify(TICKET)
    assert c.subcategory.label in WORDS[c.category.label]
    assert C._STATE.names["subcategory"] == "tfidf/svm_C1"


def test_models_are_cached_and_reused(env, monkeypatch):
    _load(env)
    assert (env["models"] / "classify_category.joblib").exists()
    monkeypatch.setattr(C, "_STATE", C._State())
    monkeypatch.setattr(C, "_fit", lambda *a, **k: pytest.fail("should load from the cache, not retrain"))
    assert _load(env) == {"category": "tfidf/svm_C1", "subcategory": "embedding/emb_lr"}


def test_classify_logs_no_ticket_text(env, caplog):
    caplog.set_level(logging.DEBUG)
    _load(env)
    C.classify(TicketIn(subject="SECRETWORD vpn", description="SECRETWORD tunnel"))
    assert "SECRETWORD" not in caplog.text


# ---- combining category and subcategory -----------------------------------------------------------------

PARENT = {"a1": "A", "a2": "A", "b1": "B"}


def test_joint_decoding_by_hand_lets_a_sure_subcategory_correct_the_category():
    # p_cat: A 0.6, B 0.4.  p_sub: a1 0.1, a2 0.1, b1 0.8.
    # joint: a1 0.6*0.1 = 0.06, a2 0.06, b1 0.4*0.8 = 0.32; total 0.44 -> B 0.32/0.44 = 0.7273, A 0.12/0.44 = 0.2727
    ranked, sub, sub_p = C.decode(np.array([0.6, 0.4]), ["A", "B"], np.array([0.1, 0.1, 0.8]), ["a1", "a2", "b1"], PARENT)
    assert [c for c, _ in ranked] == ["B", "A"]
    assert [m for _, m in ranked] == pytest.approx([0.32 / 0.44, 0.12 / 0.44])
    assert (sub, sub_p) == ("b1", pytest.approx(0.8))


def test_category_first_decoding_by_hand():
    # The category model decides A; the subcategory must be A's best (a1 and a2 tie: the first one).
    ranked, sub, _ = C.decode_category_first(np.array([0.6, 0.4]), ["A", "B"], np.array([0.1, 0.1, 0.8]),
                                             ["a1", "a2", "b1"], PARENT)
    assert ranked[0] == ("A", pytest.approx(0.6)) and sub == "a1"


def test_unknown_decoding_in_the_choice_file_is_rejected(env):
    choice = json.loads(env["choice"].read_text(encoding="utf-8"))
    env["choice"].write_text(json.dumps({**choice, "decoding": "magic"}), encoding="utf-8")
    with pytest.raises(ValueError, match="decoding"):
        _load(env)


# ---- priority rule ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("impact,expected", [("many_people", "P1"), ("cannot_work", "P2"),
                                             ("slows_me_down", "P3"), ("minor", "P4"), (None, "P3")])
def test_priority_from_impact(impact, expected):
    label, code = C.priority_by_rule(TicketIn(subject="printer slow", description="it is slow", impact=impact))
    assert label == expected
    assert code == (f"IMPACT_{impact.upper()}" if impact else "DEFAULT_P3")


def test_production_outage_is_raised_to_p2():
    t = TicketIn(subject="Payroll outage", description="payroll is down", environment="production", impact="minor")
    assert C.priority_by_rule(t) == ("P2", "OUTAGE_BUMP")


def test_outage_bump_needs_production_and_never_lowers():
    staging = TicketIn(subject="Payroll outage", description="down", environment="staging", impact="minor")
    assert C.priority_by_rule(staging) == ("P4", "IMPACT_MINOR")
    p1 = TicketIn(subject="Outage", description="everyone down", environment="production", impact="many_people")
    assert C.priority_by_rule(p1) == ("P1", "IMPACT_MANY_PEOPLE")
    no_word = TicketIn(subject="Slow page", description="loads slowly", environment="production", impact="minor")
    assert C.priority_by_rule(no_word)[0] == "P4"   # "download" etc. must not match "down": whole words only
