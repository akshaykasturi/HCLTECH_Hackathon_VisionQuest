"""Compare NB, LogisticRegression, LinearSVC and RandomForest on TF-IDF features for each label (FR-01).

  rows      app.tickets.clf_pipeline.load_splits: tickets.csv's own split column (scenario split, never re-split)
  pipeline  clf_pipeline.make_pipeline: row_text -> mask secrets -> text_tfidf (train only) -> classifier
  calibr.   every candidate sits inside CalibratedClassifierCV(method="sigmoid") so all give probabilities and
            are compared on the same footing. Its folds are StratifiedGroupKFold(3) over the TRAIN rows, grouped
            by root_cause: tickets with the same root cause stay in one fold. root_cause is a group id only,
            never a feature (row_text never reads it).
  scoring   on VAL: accuracy, macro-F1, ECE (10 equal-width bins, top-1 confidence), Brier (stats.brier),
            recall per class (P1 recall for priority), fit seconds
  winner    best val macro-F1; if the top two are within 0.01, the lower ECE wins. McNemar winner vs runner-up
            on val; bootstrap 95% CI of the winner's val macro-F1 (2000 resamples, seed 0)
  saved     backend/models/clf_<label>.joblib (git-ignored) + backend/models/clf_manifest.json
            docs/results/clf_confusion_category_val.json, section in docs/EVALUATION.md
  --final   loads the saved winners and scores TEST once. Without --final no test row is ever read.

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\compare_classifiers.py [--final]
                macOS:  .venv/bin/python scripts/compare_classifiers.py [--final]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

import joblib
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import confusion_matrix, f1_score, recall_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.naive_bayes import MultinomialNB
from sklearn.svm import LinearSVC

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.stats import brier, mcnemar  # noqa: E402
from app.common.paths import BACKEND_DIR  # noqa: E402
from app.tickets.clf_pipeline import CSV_PATH, LABELS, MODELS_DIR, TEXT_RECIPE, load_splits, make_pipeline  # noqa: E402

DOCS_DIR = BACKEND_DIR.parent / "docs"
EVAL_MD = DOCS_DIR / "EVALUATION.md"
RESULTS_DIR = DOCS_DIR / "results"
MANIFEST = "clf_manifest.json"
SEED = 0
TIE_MARGIN = 0.01
SIGNIFICANCE = 0.05            # McNemar p below this = the lead is real
CALIBRATION_ECE_GAP = 0.05     # an ECE this much lower is worth giving up a non-significant lead for
N_BOOT = 2000
SELECTION = ("val macro-F1; unless the lead is not significant (McNemar p >= 0.05) and another candidate's ECE "
             "is >= 0.05 lower (then the best-F1 such candidate); within 0.01 -> lower ECE")

# Priority is compared (as evidence) but no model is saved: a rule decides it.
RULE_LABELS = ("priority",)
PRIORITY_RULE = {
    "use": "rule",
    "reason": ("val macro-F1 0.29, P1 recall 0.00; environment, channel and service predict priority no better than "
               "the P3 majority; priority follows impact x urgency"),
}
PURITY_COLUMNS = ("environment", "channel", "affected_service")
HONESTY_NOTE = ("During development one earlier draft run (different calibration and settings) scored the test split "
                "once; that file was deleted and none of its numbers were used for any choice. --final has not been run.")
VAL_START, VAL_END = "<!-- clf-val:start -->", "<!-- clf-val:end -->"
FINAL_START, FINAL_END = "<!-- clf-final:start -->", "<!-- clf-final:end -->"


# ---------------------------------------------------------------- candidates

@dataclass(frozen=True)
class Candidate:
    name: str
    params: dict[str, Any]
    build: Callable[[], Any]


def candidates(rf_trees: int = 500) -> list[Candidate]:
    """The spec's candidates. rf_trees is lowered only by tests, to stay fast."""
    out = [Candidate("nb", {"alpha": 0.1}, lambda: MultinomialNB(alpha=0.1))]
    for c in (1, 10):
        out.append(Candidate(f"lr_C{c}", {"C": c, "class_weight": "balanced"},
                             lambda c=c: LogisticRegression(C=c, class_weight="balanced", max_iter=3000)))
    for c in (0.1, 1):
        out.append(Candidate(f"svm_C{c}", {"C": c, "class_weight": "balanced"},
                             lambda c=c: LinearSVC(C=c, class_weight="balanced", random_state=SEED)))
    rf = {"n_estimators": rf_trees, "class_weight": "balanced_subsample", "max_features": "sqrt"}
    out.append(Candidate("rf", rf, lambda: RandomForestClassifier(**rf, n_jobs=-1, random_state=SEED)))
    return out


def group_folds(train_rows: Sequence[dict], y: Sequence[str], n_splits: int = 3) -> list[tuple[np.ndarray, np.ndarray]]:
    """Precomputed calibration folds over the TRAIN rows; same root_cause -> same fold."""
    groups = [r.get("root_cause") or f"_row{i}" for i, r in enumerate(train_rows)]
    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=SEED)
    return list(sgkf.split(np.zeros(len(y)), list(y), groups))


def fit(cand: Candidate, train_rows: Sequence[dict], y: Sequence[str], folds) -> Any:
    model = make_pipeline(CalibratedClassifierCV(cand.build(), method="sigmoid", cv=folds))
    with warnings.catch_warnings():
        # Small classes cannot appear in every fold; the calibrator handles it, the warning is just noise here.
        warnings.filterwarnings("ignore", message=".*least populated class.*")
        warnings.filterwarnings("ignore", category=UserWarning, module="sklearn.model_selection")
        return model.fit(list(train_rows), list(y))


# ---------------------------------------------------------------- metrics

def ece(confidence: Sequence[float], correct: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error: 10 equal-width bins on the top-1 confidence, weighted gap |accuracy - confidence|."""
    conf = np.asarray(confidence, dtype=float)
    ok = np.asarray(correct, dtype=float)
    idx = np.minimum((conf * bins).astype(int), bins - 1)
    total = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            total += m.sum() / conf.size * abs(ok[m].mean() - conf[m].mean())
    return float(total)


def evaluate(model: Any, rows: Sequence[dict], y_true: Sequence[str]) -> dict[str, Any]:
    proba = model.predict_proba(list(rows))
    classes = np.asarray(model.classes_, dtype=object)
    pred = classes[proba.argmax(axis=1)]
    conf = proba.max(axis=1)
    t = np.asarray(y_true, dtype=object)
    correct = pred == t
    present = sorted(set(t))
    rec = recall_score(t, pred, labels=present, average=None, zero_division=0)
    return {
        "accuracy": float(correct.mean()),
        "macro_f1": float(f1_score(t, pred, average="macro", zero_division=0)),
        "ece": ece(conf, correct),
        "brier": brier(conf, correct),
        "recall": {c: float(r) for c, r in zip(present, rec)},
        "n": int(t.size),
        "_pred": pred.tolist(),
        "_correct": correct.tolist(),
    }


def bootstrap_macro_f1(y_true: Sequence[str], y_pred: Sequence[str], n: int = N_BOOT,
                       seed: int = SEED) -> tuple[float, float, float]:
    t = np.asarray(y_true, dtype=object)
    p = np.asarray(y_pred, dtype=object)
    rng = np.random.default_rng(seed)
    scores = [f1_score(t[i], p[i], average="macro", zero_division=0)
              for i in rng.integers(0, t.size, size=(n, t.size))]
    low, high = np.quantile(scores, [0.025, 0.975])
    return float(f1_score(t, p, average="macro", zero_division=0)), float(low), float(high)


def pick_winner(results: dict[str, dict[str, Any]]) -> tuple[str, Optional[str], str]:
    """(winner, runner_up, reason), decided on val only.

    1. The highest val macro-F1 leads.
    2. Calibration rule: if the leader's lead over another candidate is NOT significant (McNemar p >= 0.05 on val)
       and that candidate's ECE is at least 0.05 lower, the better-calibrated candidate wins (several qualify:
       the one with the higher macro-F1). Why: the gate acts on confidence thresholds, so a confidence that says
       90% must mean about 90% right. In the first val run RF led category and subcategory, but not significantly
       (McNemar p 0.26 and 0.08), while its ECE was 0.27-0.33. This rule was added after seeing those val
       results, before any final test scoring.
    3. Tie rule: if the top two are within 0.01 macro-F1, the lower ECE wins.
    runner_up = the best macro-F1 candidate other than the winner.
    """
    if not results:
        raise ValueError("no results to pick from")
    ranked = sorted(results, key=lambda k: (-results[k]["macro_f1"], results[k]["ece"], k))
    if len(ranked) == 1:
        return ranked[0], None, "only candidate"
    leader = ranked[0]
    lead = results[leader]

    def runner_up_of(w: str) -> str:
        return next(k for k in ranked if k != w)

    # 1. Calibration rule: the leader's lead is not significant and another candidate is much better calibrated.
    qualified = [k for k in ranked[1:]
                 if lead["ece"] - results[k]["ece"] >= CALIBRATION_ECE_GAP
                 and mcnemar(lead["_correct"], results[k]["_correct"])["p_value"] >= SIGNIFICANCE]
    if qualified:
        w = qualified[0]   # ranked is by macro-F1, so the first qualifier has the highest macro-F1
        p = mcnemar(lead["_correct"], results[w]["_correct"])["p_value"]
        return w, runner_up_of(w), (f"calibration rule: {leader}'s lead not significant (McNemar p {p:.3f}) and "
                                    f"ECE {results[w]['ece']:.3f} vs {lead['ece']:.3f}")
    # 2. Tie rule: within 0.01 macro-F1, the lower ECE wins.
    second = ranked[1]
    if lead["macro_f1"] - results[second]["macro_f1"] <= TIE_MARGIN and results[second]["ece"] < lead["ece"]:
        return second, leader, "tie rule: within 0.01 macro-F1, lower ECE"
    return leader, second, "highest val macro-F1"


def top_confusions(y_true: Sequence[str], y_pred: Sequence[str], k: int = 5) -> tuple[list[str], list[list[int]], list[dict]]:
    labels = sorted(set(y_true) | set(y_pred))
    cm = confusion_matrix(list(y_true), list(y_pred), labels=labels)
    pairs = [{"true": labels[i], "predicted": labels[j], "count": int(cm[i, j])}
             for i in range(len(labels)) for j in range(len(labels)) if i != j and cm[i, j] > 0]
    pairs.sort(key=lambda d: (-d["count"], d["true"], d["predicted"]))
    return labels, cm.tolist(), pairs[:k]


def priority_purity(train: Sequence[dict], val: Sequence[dict],
                    columns: Sequence[str] = PURITY_COLUMNS) -> dict[str, Any]:
    """How well each metadata column alone predicts priority.

    train_purity: share of train rows whose priority equals the most common priority for their column value
    (sum over values of the top count / rows). It is optimistic: a value seen a few times looks pure by chance.
    val_lookup: predict each val row with its value's train majority (unseen value -> overall train majority);
    this is the fair check, because val holds scenarios the lookup never saw."""
    def majority(counter: Counter) -> str:
        best = max(counter.values())
        return min(k for k, v in counter.items() if v == best)

    overall = Counter(r["priority"] for r in train)
    floor = majority(overall)
    y_val = [r["priority"] for r in val]

    def val_scores(pred: list[str]) -> dict[str, float]:
        n_p1 = sum(1 for y in y_val if y == "P1")
        hits_p1 = sum(1 for y, p in zip(y_val, pred) if y == p == "P1")
        return {"val_accuracy": round(sum(y == p for y, p in zip(y_val, pred)) / len(y_val), 4),
                "val_macro_f1": round(float(f1_score(y_val, pred, average="macro", zero_division=0)), 4),
                "val_p1_recall": round(hits_p1 / n_p1, 4) if n_p1 else None}

    out: dict[str, Any] = {"majority": {"label": floor, "train_share": round(overall[floor] / len(train), 4),
                                        **val_scores([floor] * len(val))}, "columns": {}}
    for col in columns:
        groups: dict[str, Counter] = defaultdict(Counter)
        for r in train:
            groups[r.get(col, "")][r["priority"]] += 1
        purity = sum(max(c.values()) for c in groups.values()) / len(train)
        pred = [majority(groups[r.get(col, "")]) if r.get(col, "") in groups else floor for r in val]
        out["columns"][col] = {"values": len(groups), "train_purity": round(purity, 4), **val_scores(pred)}
    return out


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _public(m: dict[str, Any]) -> dict[str, Any]:
    """Metrics without the per-row lists, rounded, for files and reports."""
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if not k.startswith("_")}
    out["recall"] = {c: round(r, 4) for c, r in m["recall"].items()}
    return out


# ---------------------------------------------------------------- default run (train + val only)

def run(csv_path: Path = CSV_PATH, models_dir: Path = MODELS_DIR, labels: Sequence[str] = LABELS,
        rf_trees: int = 500, results_dir: Optional[Path] = RESULTS_DIR, n_boot: int = N_BOOT,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    """Fit on train, score on val, save winners. Reads only the train and val rows."""
    data = load_splits(("train", "val"), csv_path)
    train, val = data["train"], data["val"]
    report: dict[str, Any] = {"labels": {}, "rows": {"train": len(train), "val": len(val)}}
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tickets_csv": Path(csv_path).name, "tickets_csv_sha256": sha256_of(csv_path),
        "text_recipe": TEXT_RECIPE, "selection": SELECTION, "labels": {}}
    if "priority" in labels:
        report["priority_purity"] = priority_purity(train, val)

    for label in labels:
        y_train = [r[label] for r in train]
        y_val = [r[label] for r in val]
        folds = group_folds(train, y_train)
        results: dict[str, dict[str, Any]] = {}
        cands = {c.name: c for c in candidates(rf_trees)}
        train_scores: dict[str, float] = {}
        for c in cands.values():
            start = time.perf_counter()
            model = fit(c, train, y_train, folds)
            secs = time.perf_counter() - start
            results[c.name] = {**evaluate(model, val, y_val), "fit_seconds": secs}
            # Train macro-F1 only to spot over-fitting (val far below train); never used to choose.
            train_scores[c.name] = evaluate(model, train, y_train)["macro_f1"]
            log(f"  {label:12s} {c.name:8s} val macro-F1 {results[c.name]['macro_f1']:.3f}  "
                f"acc {results[c.name]['accuracy']:.3f}  ECE {results[c.name]['ece']:.3f}  ({secs:.1f} s)")

        winner, runner_up, reason = pick_winner(results)
        w = results[winner]
        val_mc = mcnemar(w["_correct"], results[runner_up]["_correct"]) if runner_up else None
        ci = bootstrap_macro_f1(y_val, w["_pred"], n=n_boot)
        entry = {
            "winner": winner, "runner_up": runner_up, "reason": reason, "params": cands[winner].params,
            "val": _public(w), "val_macro_f1_ci95": [round(x, 4) for x in ci[1:]],
            "mcnemar_vs_runner_up_val": val_mc, "train_macro_f1": round(train_scores[winner], 4),
        }
        if label in RULE_LABELS:
            # Evidence only: the best model is too weak to ship, so nothing is saved and a rule decides.
            stale = models_dir / f"clf_{label}.joblib"
            if stale.exists():
                stale.unlink()
            manifest["labels"][label] = dict(PRIORITY_RULE)
            entry["saved"] = False
        else:
            # Refit the winner on train with the same calibration folds, and save the whole pipeline.
            final_model = fit(cands[winner], train, y_train, folds)
            models_dir.mkdir(parents=True, exist_ok=True)
            joblib.dump(final_model, models_dir / f"clf_{label}.joblib")
            entry.update(model_version=f"tfidf-{winner}-{date.today().isoformat()}", file=f"clf_{label}.joblib",
                         saved=True)
            manifest["labels"][label] = entry
        report["labels"][label] = {
            **entry,
            "candidates": {k: {**_public(v), "train_macro_f1": round(train_scores[k], 4)} for k, v in results.items()},
        }
        if label == "category":
            names, cm, top = top_confusions(y_val, w["_pred"])
            report["labels"][label]["top_confusions"] = top
            if results_dir is not None:
                results_dir.mkdir(parents=True, exist_ok=True)
                (results_dir / "clf_confusion_category_val.json").write_text(
                    json.dumps({"model": winner, "split": "val", "labels": names, "matrix": cm,
                                "top_confusions": top}, indent=2), encoding="utf-8")

    (models_dir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return report


# ---------------------------------------------------------------- --final (test, once)

class FinalRefused(RuntimeError):
    pass


def run_final(csv_path: Path = CSV_PATH, models_dir: Path = MODELS_DIR) -> dict[str, Any]:
    """Score the saved winners on TEST once. Refuses if the data or text recipe changed, or test was already scored."""
    mpath = models_dir / MANIFEST
    if not mpath.exists():
        raise FinalRefused("no saved models: run without --final first")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    if manifest.get("final"):
        raise FinalRefused(f"test was already scored at {manifest['final']['scored_at']}; it is scored only once")
    if manifest["tickets_csv_sha256"] != sha256_of(csv_path):
        raise FinalRefused("tickets.csv changed since the models were saved: rerun without --final first")
    if manifest["text_recipe"] != TEXT_RECIPE:
        raise FinalRefused("features.TEXT_RECIPE changed since the models were saved: the models are stale")

    test = load_splits(("test",), csv_path)["test"]
    out: dict[str, Any] = {"scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "rows": len(test), "labels": {}}
    for label, entry in manifest["labels"].items():
        if "file" not in entry:   # decided by a rule, no model to score
            continue
        model = joblib.load(models_dir / entry["file"])
        out["labels"][label] = {"winner": entry["winner"], "model_version": entry["model_version"],
                                "test": _public(evaluate(model, test, [r[label] for r in test]))}
    manifest["final"] = out
    mpath.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------- EVALUATION.md

def _p1(m: dict[str, Any], label: str) -> str:
    return f"{m['recall'].get('P1', 0.0):.3f}" if label == "priority" else "-"


def val_markdown(report: dict[str, Any]) -> str:
    rows = report["rows"]
    lines = [VAL_START, HONESTY_NOTE, "", "## Classifier comparison (val)", "",
             "Selection on val; test reported once with --final.", "",
             f"Train {rows['train']} tickets, val {rows['val']}. Text: `features.row_text` (subject + description), "
             "secrets masked, TF-IDF word 1-2 + char 3-5 grams fitted on train. Every model is wrapped in "
             "sigmoid calibration (3 folds over train, grouped by root_cause).", "",
             "Winner rule: highest val macro-F1, unless its lead over another candidate is not significant (McNemar "
             "p >= 0.05 on val) and that candidate's ECE is at least 0.05 lower; then the better-calibrated candidate "
             "wins (several qualify: the higher macro-F1). Within 0.01 macro-F1, the lower ECE wins. Why: the gate "
             "uses confidence thresholds, so calibration matters, and in the first val run RF's lead was not "
             "significant (McNemar p 0.26 and 0.08) while its ECE was 0.27-0.33. This rule was added after seeing "
             "val results, before any final test scoring.", "",
             "| Label | Model | Macro-F1 | Accuracy | ECE | Brier | P1 recall | Fit s |",
             "|---|---|---|---|---|---|---|---|"]
    for label, r in report["labels"].items():
        for name, m in r["candidates"].items():
            mark = " **(winner)**" if name == r["winner"] else ""
            lines.append(f"| {label} | {name}{mark} | {m['macro_f1']:.3f} | {m['accuracy']:.3f} | {m['ece']:.3f} | "
                         f"{m['brier']:.3f} | {_p1(m, label)} | {m['fit_seconds']:.1f} |")
    lines += ["", "| Label | Winner | Val macro-F1 (95% CI) | ECE | Runner-up | McNemar p vs runner-up (val) | "
              "Train macro-F1 | Why | Saved |", "|---|---|---|---|---|---|---|---|---|"]
    for label, r in report["labels"].items():
        lo, hi = r["val_macro_f1_ci95"]
        mc = r["mcnemar_vs_runner_up_val"]
        p = "-" if mc is None else f"{mc['p_value']:.3f} (b={mc['b']}, c={mc['c']})"
        saved = r.get("model_version", "no: rule") if r.get("saved") else "no: rule"
        lines.append(f"| {label} | {r['winner']} | {r['val']['macro_f1']:.3f} ({lo:.3f}-{hi:.3f}) | "
                     f"{r['val']['ece']:.3f} | {r['runner_up'] or '-'} | {p} | {r['train_macro_f1']:.3f} | "
                     f"{r['reason']} | {saved} |")
    top = report["labels"].get("category", {}).get("top_confusions")
    if top:
        lines += ["", "Top category confusions on val (true -> predicted, count):", ""]
        lines += [f"- {c['true']} -> {c['predicted']}: {c['count']}" for c in top]
    if "priority_purity" in report:
        lines += ["", priority_markdown(report)]
    lines.append(VAL_END)
    return "\n".join(lines)


def priority_markdown(report: dict[str, Any]) -> str:
    pur = report["priority_purity"]
    pr = report["labels"]["priority"]
    best = pr["val"]
    maj = pur["majority"]
    lines = ["### Priority: why a rule, not a model", "",
             f"No priority model is saved (`clf_manifest.json`: `labels.priority.use = \"rule\"`). The best text model "
             f"({pr['winner']}) reaches val macro-F1 {best['macro_f1']:.2f} and P1 recall "
             f"{best['recall'].get('P1', 0.0):.2f} (train macro-F1 {pr['train_macro_f1']:.2f}: it fits noise). "
             f"Always answering {maj['label']} gets val accuracy {maj['val_accuracy']:.3f}.", "",
             "Can a metadata column predict priority on its own? Train purity = share of train rows whose priority "
             "is the most common one for their value (optimistic). Val lookup = predict each val ticket with its "
             "value's train majority (the fair check: val holds scenarios the lookup never saw).", "",
             "| Column | Values | Train purity | Val accuracy | Val macro-F1 | Val P1 recall |",
             "|---|---|---|---|---|---|",
             f"| (majority {maj['label']}) | 1 | {maj['train_share']:.3f} | {maj['val_accuracy']:.3f} | "
             f"{maj['val_macro_f1']:.3f} | {maj['val_p1_recall']:.3f} |"]
    for col, c in pur["columns"].items():
        lines.append(f"| {col} | {c['values']} | {c['train_purity']:.3f} | {c['val_accuracy']:.3f} | "
                     f"{c['val_macro_f1']:.3f} | {c['val_p1_recall']:.3f} |")
    cols = pur["columns"]
    beats = [k for k, c in cols.items() if c["val_accuracy"] > maj["val_accuracy"]]
    finds_p1 = [k for k, c in cols.items() if (c["val_p1_recall"] or 0) > 0]
    overfit = [k for k, c in cols.items() if c["train_purity"] - c["val_accuracy"] >= 0.1]
    verdict = ("No column beats the majority's val accuracy" if not beats
               else f"Only {', '.join(beats)} beat the majority's val accuracy")
    verdict += " and none finds a single P1." if not finds_p1 else f"; P1 is found by {', '.join(finds_p1)}."
    if overfit:
        verdict += (f" {', '.join(overfit)}: high purity on train, much lower accuracy on val. Rare values look pure by chance, "
                    "and the purity does not carry over to unseen scenarios.")
    lines += ["", verdict + " Priority follows impact x urgency, which the text and these columns do not carry, so "
              "priority comes from a rule (TicketIn.impact, plus raising production outages to P1/P2), never from "
              "a confident ML label."]
    return "\n".join(lines)


def final_markdown(final: dict[str, Any]) -> str:
    lines = [FINAL_START, "## Classifier comparison (test, FINAL)", "",
             f"Scored once at {final['scored_at']} on {final['rows']} test tickets, with the winners frozen on val.", "",
             "| Label | Model | Macro-F1 | Accuracy | ECE | Brier | P1 recall |", "|---|---|---|---|---|---|---|"]
    for label, r in final["labels"].items():
        m = r["test"]
        lines.append(f"| {label} | {r['model_version']} | {m['macro_f1']:.3f} | {m['accuracy']:.3f} | "
                     f"{m['ece']:.3f} | {m['brier']:.3f} | {_p1(m, label)} |")
    lines.append(FINAL_END)
    return "\n".join(lines)


def write_section(path: Path, start: str, end: str, block: str) -> None:
    """Replace the block between the markers, or append it. Other sections of the file are left alone."""
    text = path.read_text(encoding="utf-8") if path.exists() else "# Evaluation\n"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if pattern.search(text):
        text = pattern.sub(lambda _: block, text)
    else:
        text = text.rstrip("\n") + "\n\n" + block + "\n"
    path.write_text(text, encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare TF-IDF classifiers on tickets.csv (val), or score test once.")
    parser.add_argument("--final", action="store_true", help="score the saved winners on TEST (only once)")
    args = parser.parse_args(argv)

    if args.final:
        try:
            final = run_final()
        except FinalRefused as exc:
            print(f"REFUSED: {exc}")
            return 1
        write_section(EVAL_MD, FINAL_START, FINAL_END, final_markdown(final))
        print(final_markdown(final))
        return 0

    report = run()
    write_section(EVAL_MD, VAL_START, VAL_END, val_markdown(report))
    print()
    print(val_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
