"""Compare three classifiers on the ticket embeddings, on the VALIDATION split (evaluation report only).

The app does not use these models. Test is never read: rows of the test split are dropped right after loading.

  features  the 384 embedding numbers per ticket (data/processed/ticket_embeddings.npz, from embed_tickets.py)
  targets   category, subcategory, priority: one run each
  models    LogisticRegression; LinearSVC inside CalibratedClassifierCV (FR-01 needs a confidence, and a plain
            SVM gives none); RandomForestClassifier. random_state=42 everywhere.
  tuning    GridSearchCV on TRAIN only, 5 folds, scoring macro-F1. The scaler sits inside the pipeline, so in
            every fold it is fitted on that fold's training part only.
  compared  on VAL: accuracy, macro/weighted precision-recall-F1, per-class TP/FP/FN/TN and report, confusion
            matrix, Brier score of the top-class confidence, fit and predict time
  references  majority label, and TF-IDF + LogisticRegression on the text (app/analysis/baselines.py)
  winner    highest val macro-F1 among the three; McNemar test against the runner-up (and against TF-IDF)

Outputs: docs/ml/embedding-models-val.json (everything) and docs/ml/embedding-models-val.md (summary).

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\compare_embedding_models.py
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.baselines import majority_baseline, text_only_baseline  # noqa: E402
from app.analysis.stats import brier, mcnemar  # noqa: E402
from app.common.paths import BACKEND_DIR, DATA_DIR  # noqa: E402
from app.tickets.features import TEXT_RECIPE, row_text  # noqa: E402

SEED = 42
TARGETS = ("category", "subcategory", "priority")
CSV_PATH = DATA_DIR / "tickets" / "tickets.csv"
NPZ_PATH = DATA_DIR / "processed" / "ticket_embeddings.npz"
OUT_DIR = BACKEND_DIR.parent / "docs" / "ml"
OUT_NAME = "embedding-models-val"
RERUN = "rerun scripts/embed_tickets.py"


class StaleEmbeddingsError(RuntimeError):
    """The saved embeddings are missing or were built from a different tickets.csv / text recipe."""


# ---- 1. Load: embeddings + labels, aligned by ticket_id, train and val only --------------------------

@dataclass
class Split:
    X: np.ndarray                    # (n, dim) embeddings
    ids: list[str]
    labels: dict[str, list[str]]     # target -> labels, same order as ids
    texts: list[str]                 # ticket_text(), for the TF-IDF reference only


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_splits(csv_path: Path = CSV_PATH, npz_path: Path = NPZ_PATH) -> tuple[Split, Split, dict[str, Any]]:
    """(train, val, meta). Stops with StaleEmbeddingsError if the embeddings don't belong to this CSV.
    Test rows are dropped here and never returned."""
    meta_path = npz_path.with_name(npz_path.stem + ".meta.json")
    if not npz_path.exists() or not meta_path.exists():
        raise StaleEmbeddingsError(f"{npz_path.name} or its meta file is missing: {RERUN}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("tickets_csv_sha256") != _sha256(csv_path):
        raise StaleEmbeddingsError(f"embeddings were built from a different tickets.csv (sha256 differs): {RERUN}")
    if meta.get("text_recipe") != TEXT_RECIPE:
        raise StaleEmbeddingsError(f"embeddings use an older text recipe ({meta.get('text_recipe')!r}): {RERUN}")

    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        rows = {r["ticket_id"]: r for r in csv.DictReader(f)}
    data = np.load(npz_path)
    ids = [str(t) for t in data["ticket_id"]]
    splits = [str(s) for s in data["split"]]
    if set(ids) != set(rows) or len(ids) != len(rows):
        raise StaleEmbeddingsError(f"ticket_ids in the npz don't match tickets.csv: {RERUN}")

    out = {}
    for name in ("train", "val"):
        keep = [i for i, s in enumerate(splits) if s == name]
        for i in keep:  # the split stored with the vector must be the CSV's split
            if rows[ids[i]]["split"] != name:
                raise StaleEmbeddingsError(f"{ids[i]} is {rows[ids[i]]['split']} in the CSV but {name} in the npz: {RERUN}")
        kept_ids = [ids[i] for i in keep]
        out[name] = Split(
            X=np.asarray(data["vectors"][keep], dtype=np.float64),
            ids=kept_ids,
            labels={t: [rows[k][t] for k in kept_ids] for t in TARGETS},
            texts=[row_text(rows[k]) for k in kept_ids])
    assert out["train"].X.shape[0] and out["val"].X.shape[0], "train or val is empty"
    return out["train"], out["val"], meta


# ---- 2. Models and their small grids ------------------------------------------------------------------

def model_grids(cv: int = 5) -> dict[str, tuple[Any, dict[str, list]]]:
    """name -> (unfitted estimator, GridSearchCV grid)."""
    svm = CalibratedClassifierCV(LinearSVC(random_state=SEED, max_iter=10000), method="sigmoid", cv=3)
    return {
        "logistic_regression": (
            Pipeline([("scale", StandardScaler()), ("clf", LogisticRegression(max_iter=5000, random_state=SEED))]),
            {"clf__C": [0.1, 1, 10]}),
        "linear_svm_calibrated": (
            Pipeline([("scale", StandardScaler()), ("clf", svm)]),
            {"clf__estimator__C": [0.1, 1, 10]}),
        "random_forest": (   # trees don't need scaling
            RandomForestClassifier(random_state=SEED, n_jobs=-1),
            {"n_estimators": [200, 500], "max_depth": [None, 20]}),
    }


# ---- 3. Metrics -----------------------------------------------------------------------------------------

def one_vs_rest(cm: np.ndarray, labels: Sequence[str]) -> dict[str, dict[str, int]]:
    """Per class from the confusion matrix (rows = true, columns = predicted):
    TP = the diagonal cell; FP = rest of its column; FN = rest of its row; TN = everything else.
    TP + FP + FN + TN = number of rows, for every class."""
    total = int(cm.sum())
    out = {}
    for j, label in enumerate(labels):
        tp = int(cm[j, j])
        fp = int(cm[:, j].sum()) - tp
        fn = int(cm[j, :].sum()) - tp
        out[label] = {"TP": tp, "FP": fp, "FN": fn, "TN": total - tp - fp - fn}
    return out


def _safe(num: float, den: float) -> float:
    return float(num / den) if den else 0.0


def summary_metrics(cm: np.ndarray, labels: Sequence[str]) -> dict[str, float]:
    """Accuracy and macro / weighted precision, recall, F1, all from the confusion matrix
    (0 when a class is never predicted or never present, as sklearn does with zero_division=0)."""
    ovr = one_vs_rest(cm, labels)
    support = cm.sum(axis=1).astype(float)
    p = np.array([_safe(c["TP"], c["TP"] + c["FP"]) for c in ovr.values()])
    r = np.array([_safe(c["TP"], c["TP"] + c["FN"]) for c in ovr.values()])
    f = np.array([_safe(2 * pi * ri, pi + ri) for pi, ri in zip(p, r)])
    w = support / support.sum() if support.sum() else np.zeros_like(support)
    return {"accuracy": _safe(np.trace(cm), cm.sum()),
            "precision_macro": float(p.mean()), "recall_macro": float(r.mean()), "f1_macro": float(f.mean()),
            "precision_weighted": float((p * w).sum()), "recall_weighted": float((r * w).sum()),
            "f1_weighted": float((f * w).sum())}


def most_confused(cm: np.ndarray, labels: Sequence[str], n: int = 5) -> list[dict[str, Any]]:
    pairs = [(int(cm[i, j]), labels[i], labels[j]) for i in range(len(labels)) for j in range(len(labels))
             if i != j and cm[i, j] > 0]
    pairs.sort(key=lambda x: (-x[0], x[1], x[2]))
    return [{"true": t, "predicted": p, "count": c} for c, t, p in pairs[:n]]


def evaluate(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str],
             top_conf: Optional[Sequence[float]] = None) -> dict[str, Any]:
    """Everything for one model on one target."""
    labels = list(labels)
    cm = confusion_matrix(list(y_true), list(y_pred), labels=labels)
    correct = np.asarray(y_true, dtype=object) == np.asarray(y_pred, dtype=object)
    out: dict[str, Any] = summary_metrics(cm, labels)
    out.update({
        "per_class_counts": one_vs_rest(cm, labels),
        "per_class_report": classification_report(list(y_true), list(y_pred), labels=labels, output_dict=True,
                                                  zero_division=0),
        "confusion_matrix": {"labels": labels, "rows_true_cols_predicted": cm.tolist()},
        "most_confused": most_confused(cm, labels),
        "brier_top_class": None if top_conf is None else brier(top_conf, correct),
        "correct": correct.astype(int).tolist(),   # per val row, for McNemar
    })
    return out


# ---- 4. One target ----------------------------------------------------------------------------------

def run_target(target: str, train: Split, val: Split, grids: dict[str, tuple[Any, dict[str, list]]],
               cv: int = 5) -> dict[str, Any]:
    y_tr, y_va = train.labels[target], val.labels[target]
    labels = sorted(set(y_tr) | set(y_va))
    folds = StratifiedKFold(n_splits=cv, shuffle=True, random_state=SEED)
    result: dict[str, Any] = {"labels": labels, "n_train": len(y_tr), "n_val": len(y_va), "models": {},
                              "references": {}}

    for name, (estimator, grid) in grids.items():
        search = GridSearchCV(estimator, grid, cv=folds, scoring="f1_macro", n_jobs=-1, refit=False)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            search.fit(train.X, y_tr)                  # TRAIN only
            best = clone(estimator).set_params(**search.best_params_)
            t0 = time.perf_counter()
            best.fit(train.X, y_tr)                    # refit the best settings on all of train
            fit_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        proba = best.predict_proba(val.X)
        classes = [str(c) for c in best.classes_]
        pred = [classes[i] for i in proba.argmax(axis=1)]   # the class it is most sure of = its answer
        predict_s = time.perf_counter() - t0
        m = evaluate(y_va, pred, labels, proba.max(axis=1))
        m.update({"best_params": search.best_params_, "cv_f1_macro_train": float(search.best_score_),
                  "fit_seconds": round(fit_s, 3), "predict_seconds": round(predict_s, 4)})
        result["models"][name] = m

    # References on the same val rows.
    maj = majority_baseline(y_tr, len(y_va))
    result["references"]["majority"] = evaluate(y_va, maj, labels, [1.0] * len(y_va)) | {
        "fit_seconds": 0.0, "predict_seconds": 0.0}
    t0 = time.perf_counter()
    tfidf = text_only_baseline(train.texts, y_tr, val.texts)
    fit_s = time.perf_counter() - t0
    result["references"]["tfidf_logistic_regression"] = evaluate(
        y_va, [str(p) for p in tfidf.predictions], labels, tfidf.probabilities.max(axis=1)) | {
        "fit_seconds": round(fit_s, 3), "predict_seconds": None, "note": "fit time includes predicting val"}

    # Winner = highest val macro-F1 among the three embedding models (ties: the faster fit).
    ranked = sorted(result["models"], key=lambda n: (-result["models"][n]["f1_macro"],
                                                     result["models"][n]["fit_seconds"]))
    winner, runner_up = ranked[0], ranked[1] if len(ranked) > 1 else None
    result["winner"] = winner
    if runner_up:
        result["mcnemar_vs_runner_up"] = _mcnemar(result["models"][winner], result["models"][runner_up],
                                                  winner, runner_up)
    result["mcnemar_vs_tfidf"] = _mcnemar(result["models"][winner],
                                          result["references"]["tfidf_logistic_regression"], winner,
                                          "tfidf_logistic_regression")
    return result


def _mcnemar(a: dict[str, Any], b: dict[str, Any], a_name: str, b_name: str) -> dict[str, Any]:
    t = mcnemar(a["correct"], b["correct"])
    better = a_name if t["b"] > t["c"] else b_name
    t.update({"a": a_name, "b_model": b_name, "significant_at_0_05": t["p_value"] < 0.05,
              "better_on_disagreements": better if t["b"] != t["c"] else None,
              "plain": (f"{a_name} vs {b_name}: {t['b']} val tickets only {a_name} got right, {t['c']} only "
                        f"{b_name} got right; p = {t['p_value']:.3g} -> "
                        + (f"a real difference, in favour of {better} (p < 0.05)." if t["p_value"] < 0.05
                           else "could be chance (p >= 0.05)."))})
    return t


# ---- 5. Report --------------------------------------------------------------------------------------

def _row(name: str, m: dict[str, Any]) -> str:
    brier_s = "-" if m.get("brier_top_class") is None else f"{m['brier_top_class']:.3f}"
    fit = "-" if m.get("fit_seconds") is None else f"{m['fit_seconds']:.2f} s"
    return (f"| {name} | {m['accuracy']:.3f} | {m['precision_macro']:.3f} | {m['recall_macro']:.3f} | "
            f"{m['f1_macro']:.3f} | {brier_s} | {fit} |")


def markdown(report: dict[str, Any]) -> str:
    lines = ["# Embedding classifiers on the validation split", "",
             f"Generated {report['created_at']} by `backend/scripts/compare_embedding_models.py`. "
             f"Features: {report['embedding_model']} embeddings ({report['dim']} numbers) of "
             "`ticket_text(subject, description)`. Tuned on train (5-fold GridSearchCV, macro-F1), compared on "
             "val. **Test was not used.** For the evaluation report only: the app does not use these models.", "",
             "Brier = top-class confidence vs right/wrong (0 is perfect). Majority's confidence is set to 1, so its "
             "Brier = its error rate. TF-IDF fit time includes predicting val.", ""]
    for target, r in report["targets"].items():
        lines += [f"## {target}", "", f"{r['n_train']} train / {r['n_val']} val tickets, {len(r['labels'])} classes.",
                  "", "| model | accuracy | macro-P | macro-R | macro-F1 | Brier | fit time |",
                  "|---|---|---|---|---|---|---|"]
        for name, m in r["models"].items():
            lines.append(_row(name + (" **(winner)**" if name == r["winner"] else ""), m))
        for name, m in r["references"].items():
            lines.append(_row(f"_ref: {name}_", m))
        lines += ["", f"- {r['mcnemar_vs_runner_up']['plain']}" if "mcnemar_vs_runner_up" in r else "",
                  f"- {r['mcnemar_vs_tfidf']['plain']}", ""]
        w = r["models"][r["winner"]]
        labels = w["confusion_matrix"]["labels"]
        cm = w["confusion_matrix"]["rows_true_cols_predicted"]
        lines += [f"Confusion matrix of the winner ({r['winner']}), rows = true, columns = predicted:", "",
                  "| true \\ predicted | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
        lines += [f"| **{lab}** | " + " | ".join(str(v) for v in row) + " |" for lab, row in zip(labels, cm)]
        lines += ["", "Most confused pairs (true -> predicted):", ""]
        lines += [f"{i}. {p['true']} -> {p['predicted']}: {p['count']}" for i, p in enumerate(w["most_confused"], 1)]
        if not w["most_confused"]:
            lines.append("none")
        lines.append("")
    return "\n".join(lines)


def run(csv_path: Path = CSV_PATH, npz_path: Path = NPZ_PATH, out_dir: Path = OUT_DIR,
        targets: Sequence[str] = TARGETS, grids: Optional[dict] = None, cv: int = 5) -> dict[str, Any]:
    train, val, meta = load_splits(csv_path, npz_path)
    report = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split_compared": "val",
              "test_used": False, "embedding_model": meta.get("model"), "dim": int(train.X.shape[1]),
              "tickets_csv_sha256": meta.get("tickets_csv_sha256"), "seed": SEED, "cv_folds": cv,
              "targets": {}}
    for target in targets:
        report["targets"][target] = run_target(target, train, val, grids or model_grids(cv), cv)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{OUT_NAME}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out_dir / f"{OUT_NAME}.md").write_text(markdown(report), encoding="utf-8")
    try:
        import matplotlib  # noqa: F401  (not a dependency: heatmaps only if it happens to be installed)
        _heatmaps(report, out_dir)
    except ImportError:
        pass
    return report


def _heatmaps(report: dict[str, Any], out_dir: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for target, r in report["targets"].items():
        cmd = r["models"][r["winner"]]["confusion_matrix"]
        fig, ax = plt.subplots(figsize=(max(5, len(cmd["labels"]) * 0.5),) * 2)
        ax.imshow(cmd["rows_true_cols_predicted"], cmap="Blues")
        ax.set_xticks(range(len(cmd["labels"])), cmd["labels"], rotation=90)
        ax.set_yticks(range(len(cmd["labels"])), cmd["labels"])
        ax.set_xlabel("predicted"), ax.set_ylabel("true"), ax.set_title(f"{target}: {r['winner']} (val)")
        fig.tight_layout()
        fig.savefig(out_dir / f"{OUT_NAME}-{target}-confusion.png", dpi=120)
        plt.close(fig)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare embedding classifiers on the val split.")
    parser.add_argument("--targets", nargs="+", default=list(TARGETS), choices=list(TARGETS))
    args = parser.parse_args(argv)
    try:
        report = run(targets=args.targets)
    except StaleEmbeddingsError as exc:
        print(f"STOPPED: {exc}")
        return 1
    for target, r in report["targets"].items():
        print(f"\n== {target} ({r['n_train']} train / {r['n_val']} val, {len(r['labels'])} classes)")
        print("| model | accuracy | macro-P | macro-R | macro-F1 | Brier | fit time |")
        for name, m in {**r["models"], **{f"ref: {k}": v for k, v in r["references"].items()}}.items():
            print(_row(name, m))
        print(f"winner: {r['winner']}")
        if "mcnemar_vs_runner_up" in r:
            print("  " + r["mcnemar_vs_runner_up"]["plain"])
        print("  " + r["mcnemar_vs_tfidf"]["plain"])
    print(f"\nSaved {OUT_DIR / (OUT_NAME + '.json')} and .md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
