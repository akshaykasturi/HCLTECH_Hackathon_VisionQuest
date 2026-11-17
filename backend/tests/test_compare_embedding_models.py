"""scripts/compare_embedding_models.py on tiny made-up vectors (no model, no real data)."""
import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from app.tickets.features import TEXT_RECIPE

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "compare_embedding_models.py"
DIM = 8


@pytest.fixture(scope="module")
def cmp():
    spec = importlib.util.spec_from_file_location("compare_embedding_models", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module   # @dataclass looks its module up here
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


def _dataset(tmp_path: Path) -> tuple[Path, Path]:
    """3 well-separated classes: 12 train + 6 val each. 3 test rows hold NaN vectors and a TEST_ONLY label,
    so anything that reads the test split crashes or shows that label."""
    rng = np.random.default_rng(0)
    centres = np.eye(DIM)[:3] * 3
    words = {"Network": "vpn tunnel error", "Email": "mailbox outlook quota", "Access": "password locked account"}
    rows, vectors = [], []
    for split, n in (("train", 12), ("val", 6)):
        for k, cat in enumerate(words):
            for i in range(n):
                tid = f"{split[:2]}-{cat}-{i}"
                rows.append({"ticket_id": tid, "split": split, "category": cat, "subcategory": f"{cat}-sub",
                             "priority": "P2" if i % 2 else "P3", "subject": f"{words[cat]} {i}",
                             "description": f"{words[cat]} please help"})
                v = centres[k] + rng.normal(0, 0.3, DIM)
                vectors.append(v / np.linalg.norm(v))
    for i in range(3):
        rows.append({"ticket_id": f"te-{i}", "split": "test", "category": "TEST_ONLY", "subcategory": "TEST_ONLY",
                     "priority": "TEST_ONLY", "subject": "secret exam question", "description": "do not read"})
        vectors.append(np.full(DIM, np.nan))

    csv_path = tmp_path / "tickets.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    npz = tmp_path / "ticket_embeddings.npz"
    np.savez(npz, vectors=np.array(vectors, dtype=np.float32), ticket_id=np.array([r["ticket_id"] for r in rows]),
             split=np.array([r["split"] for r in rows]))
    meta = {"model": "fake", "dim": DIM, "rows": len(rows), "text_recipe": TEXT_RECIPE,
            "tickets_csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest()}
    (tmp_path / "ticket_embeddings.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return csv_path, npz


def _small_grids(cmp):
    """The real models with one setting each and few trees, so the test runs in seconds."""
    g = cmp.model_grids(cv=3)
    return {"logistic_regression": (g["logistic_regression"][0], {"clf__C": [1]}),
            "linear_svm_calibrated": (g["linear_svm_calibrated"][0], {"clf__estimator__C": [1]}),
            "random_forest": (g["random_forest"][0], {"n_estimators": [20], "max_depth": [None]})}


@pytest.fixture(scope="module")
def report(cmp, tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cmp")
    csv_path, npz = _dataset(tmp)
    rep = cmp.run(csv_path, npz, tmp / "out", targets=["category"], grids=_small_grids(cmp), cv=3)
    return rep, tmp / "out"


# ---- metrics -------------------------------------------------------------------------------------------

Y_TRUE = ["a", "a", "a", "b", "b", "c", "c", "c", "c", "d"]
Y_PRED = ["a", "b", "a", "b", "c", "c", "c", "a", "c", "c"]   # "d" is never predicted
LABELS = ["a", "b", "c", "d"]


def test_tp_fp_fn_tn_add_up_to_the_row_count_for_every_class(cmp):
    cm = confusion_matrix(Y_TRUE, Y_PRED, labels=LABELS)
    counts = cmp.one_vs_rest(cm, LABELS)
    for c in counts.values():
        assert c["TP"] + c["FP"] + c["FN"] + c["TN"] == len(Y_TRUE)
    # "c" by hand: 3 right (TP); predicted c for one b and one d (FP 2); one true c called a (FN 1); TN = 10-6 = 4
    assert counts["c"] == {"TP": 3, "FP": 2, "FN": 1, "TN": 4}


def test_summary_metrics_match_sklearn(cmp):
    m = cmp.summary_metrics(confusion_matrix(Y_TRUE, Y_PRED, labels=LABELS), LABELS)
    assert m["accuracy"] == pytest.approx(accuracy_score(Y_TRUE, Y_PRED))
    for avg in ("macro", "weighted"):
        p, r, f, _ = precision_recall_fscore_support(Y_TRUE, Y_PRED, labels=LABELS, average=avg, zero_division=0)
        assert (m[f"precision_{avg}"], m[f"recall_{avg}"], m[f"f1_{avg}"]) == pytest.approx((p, r, f))


def test_most_confused_pairs(cmp):
    cm = confusion_matrix(Y_TRUE, Y_PRED, labels=LABELS)
    pairs = cmp.most_confused(cm, LABELS)
    assert len(pairs) == 4 and all(p["count"] == 1 for p in pairs)   # a->b, b->c, c->a, d->c
    assert {(p["true"], p["predicted"]) for p in pairs} == {("a", "b"), ("b", "c"), ("c", "a"), ("d", "c")}


# ---- the whole run on tiny data ------------------------------------------------------------------------

def test_run_compares_three_models_and_two_references(report):
    rep, out = report
    r = rep["targets"]["category"]
    assert set(r["models"]) == {"logistic_regression", "linear_svm_calibrated", "random_forest"}
    assert set(r["references"]) == {"majority", "tfidf_logistic_regression"}
    assert r["n_train"] == 36 and r["n_val"] == 18
    assert r["winner"] in r["models"]
    assert r["models"][r["winner"]]["f1_macro"] == max(m["f1_macro"] for m in r["models"].values())
    for m in r["models"].values():
        assert 0 <= m["brier_top_class"] <= 1 and m["fit_seconds"] >= 0
        for c in m["per_class_counts"].values():
            assert sum(c.values()) == r["n_val"]
    assert {"p_value", "significant_at_0_05", "plain"} <= set(r["mcnemar_vs_runner_up"])
    assert (out / "embedding-models-val.json").exists() and (out / "embedding-models-val.md").exists()


def test_test_split_is_never_read(report):
    rep, out = report
    r = rep["targets"]["category"]
    assert "TEST_ONLY" not in r["labels"]          # labels come from train + val only
    assert r["n_train"] + r["n_val"] == 54         # the 3 test rows are not counted anywhere
    text = (out / "embedding-models-val.json").read_text(encoding="utf-8")
    assert "TEST_ONLY" not in text and "te-0" not in text
    assert rep["test_used"] is False


def test_load_splits_drops_test_rows(cmp, tmp_path):
    csv_path, npz = _dataset(tmp_path)
    train, val, _ = cmp.load_splits(csv_path, npz)
    assert np.isfinite(train.X).all() and np.isfinite(val.X).all()   # the NaN test vectors were never loaded
    assert not any(i.startswith("te-") for i in train.ids + val.ids)


# ---- stale embeddings ------------------------------------------------------------------------------------

def test_stale_npz_check_fires_when_the_csv_changed(cmp, tmp_path):
    csv_path, npz = _dataset(tmp_path)
    csv_path.write_text(csv_path.read_text(encoding="utf-8") + "", encoding="utf-8")   # unchanged: fine
    cmp.load_splits(csv_path, npz)
    with csv_path.open("a", encoding="utf-8") as f:
        f.write("new-1,train,Network,x,P2,added later,after embedding\n")
    with pytest.raises(cmp.StaleEmbeddingsError, match="rerun scripts/embed_tickets.py"):
        cmp.load_splits(csv_path, npz)


def test_stale_check_fires_when_npz_missing_or_recipe_old(cmp, tmp_path):
    csv_path, npz = _dataset(tmp_path)
    meta_path = tmp_path / "ticket_embeddings.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta_path.write_text(json.dumps({**meta, "text_recipe": "v0"}), encoding="utf-8")
    with pytest.raises(cmp.StaleEmbeddingsError, match="older text recipe"):
        cmp.load_splits(csv_path, npz)
    npz.unlink()
    with pytest.raises(cmp.StaleEmbeddingsError, match="rerun scripts/embed_tickets.py"):
        cmp.load_splits(csv_path, npz)


def test_main_prints_rerun_message_and_exits_1(cmp, tmp_path, monkeypatch, capsys):
    csv_path, npz = _dataset(tmp_path)
    npz.unlink()
    real_run = cmp.run
    monkeypatch.setattr(cmp, "run", lambda targets: real_run(csv_path, npz, tmp_path / "out", targets))
    assert cmp.main([]) == 1
    assert "rerun scripts/embed_tickets.py" in capsys.readouterr().out
