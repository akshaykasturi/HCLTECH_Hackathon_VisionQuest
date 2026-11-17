"""scripts/compare_all_classifiers.py on tiny made-up data (saved fake vectors, no model download)."""
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from app.tickets.features import TEXT_RECIPE

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_all_classifiers.py"
CATS = {"Network": "vpn tunnel error connect", "Email": "mailbox quota outlook full", "Access": "password reset login"}


@pytest.fixture(scope="module")
def cmp():
    spec = importlib.util.spec_from_file_location("compare_all_classifiers", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("all")
    rng = np.random.default_rng(0)
    rows, vecs = [], []
    for split, n in (("train", 12), ("val", 5)):
        for k, (cat, words) in enumerate(CATS.items()):
            for i in range(n):
                rows.append({"ticket_id": f"{split}-{cat}-{i}", "split": split, "category": cat,
                             "subcategory": f"{cat}-sub", "priority": "P2" if i % 2 else "P3",
                             "root_cause": f"{cat}-rc{i % 4}", "subject": f"{words} {i}", "description": words})
                v = np.eye(16)[k] * 3 + rng.normal(0, 0.3, 16)
                vecs.append(v / np.linalg.norm(v))
    for i in range(3):   # test rows: NaN vectors and a label that must never show up
        rows.append({"ticket_id": f"te-{i}", "split": "test", "category": "TEST_ONLY", "subcategory": "TEST_ONLY",
                     "priority": "TEST_ONLY", "root_cause": "x", "subject": "exam", "description": "exam"})
        vecs.append(np.full(16, np.nan))
    csv_path = tmp / "tickets.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    npz = tmp / "ticket_embeddings.npz"
    np.savez(npz, vectors=np.array(vecs, dtype=np.float32), ticket_id=np.array([r["ticket_id"] for r in rows]),
             split=np.array([r["split"] for r in rows]))
    (tmp / "ticket_embeddings.meta.json").write_text(json.dumps(
        {"text_recipe": TEXT_RECIPE, "tickets_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()}),
        encoding="utf-8")
    return csv_path, npz, tmp


@pytest.fixture(scope="module")
def report(cmp, data):
    csv_path, npz, tmp = data
    return cmp.run(csv_path, npz, labels=["category", "priority"], results_file=tmp / "none.json", rf_trees=10,
                   log=lambda s: None)


def test_all_nine_candidates_are_compared(report):
    r = report["labels"]["category"]
    assert len(r["candidates"]) == 9
    assert {m["by"] for m in r["candidates"].values()} == {"Akhilesh", "Raswanth"}
    assert sum(m["kind"] == "embedding" for m in r["candidates"].values()) == 3
    assert r["winner"] in r["candidates"] and r["fallback_tfidf"] in r["candidates"]
    assert r["candidates"][r["fallback_tfidf"]]["kind"] == "tfidf"


def test_tfidf_scores_match_akhileshs_own_code(cmp, data, report):
    csv_path, _, _ = data
    from app.tickets.clf_pipeline import load_splits
    d = load_splits(("train", "val"), csv_path)
    y_tr = [r["category"] for r in d["train"]]
    cand = {c.name: c for c in cmp.akh.candidates()}["svm_C1"]
    his = cmp.akh.fit(cand, d["train"], y_tr, cmp.akh.group_folds(d["train"], y_tr))
    m = cmp.akh.evaluate(his, d["val"], [r["category"] for r in d["val"]])
    assert report["labels"]["category"]["candidates"]["svm_C1"]["macro_f1"] == pytest.approx(m["macro_f1"], abs=1e-4)


def test_test_split_is_never_read(report):
    text = json.dumps(report)
    assert "TEST_ONLY" not in text and "te-0" not in text
    assert report["test_used"] is False and report["rows"] == {"train": 36, "val": 15}


def test_choice_file_has_primary_fallback_and_priority_rule(cmp, report, data):
    _, _, tmp = data
    ch = cmp.choice(report, tmp / "none.json")
    cat = ch["labels"]["category"]
    assert cat["use"] == "model" and cat["fallback"]["kind"] == "tfidf"
    assert cat["primary"]["name"] == report["labels"]["category"]["winner"]
    assert ch["labels"]["priority"]["use"] == "rule"
    assert report["labels"]["priority"]["used_by_app"] == "rule"


def test_both_decodings_are_compared_when_both_labels_run(cmp, data):
    csv_path, npz, tmp = data
    rep = cmp.run(csv_path, npz, labels=["category", "subcategory"], results_file=tmp / "none.json", rf_trees=10,
                  log=lambda s: None)
    dec = rep["decoding"]
    assert set(dec["candidates"]) == {"joint", "category_first"} and dec["winner"] in dec["candidates"]
    assert cmp.choice(rep, tmp / "none.json")["decoding"] == dec["winner"]
    assert "Combining the two winners" in cmp.markdown(rep)


def test_stale_embeddings_stop_the_run(cmp, data, tmp_path):
    csv_path, npz, _ = data
    changed = tmp_path / "tickets.csv"
    changed.write_text(csv_path.read_text(encoding="utf-8") + "extra,train,Network,x,P3,rc,a,b\n", encoding="utf-8")
    with pytest.raises(cmp.StaleEmbeddingsError, match="rerun scripts/embed_tickets.py"):
        cmp.run(changed, npz, labels=["category"], log=lambda s: None)
