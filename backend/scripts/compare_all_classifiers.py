"""All classifier candidates side by side on the VALIDATION split, and the choice the app uses (FR-01).

  TF-IDF   Akhilesh's 6 candidates (scripts/compare_classifiers.py): NB, LR C1/C10, LinearSVC C0.1/C1, RF
  embed    Raswanth's 3 candidates (scripts/compare_embedding_models.py, settings tuned on train only):
           LR, LinearSVC, RF on the bge-small embeddings of features.row_text
  footing  identical for all 9: every model inside CalibratedClassifierCV(method="sigmoid") with Akhilesh's
           calibration folds (StratifiedGroupKFold(3) over TRAIN, grouped by root_cause); same val rows; the
           same evaluate() (accuracy, macro-F1, ECE, Brier, recall per class)
  winner   Akhilesh's documented rule (compare_classifiers.pick_winner), now over all 9: highest val macro-F1,
           unless that lead is not significant (McNemar p >= 0.05) and another candidate's ECE is >= 0.05 lower;
           within 0.01 macro-F1 the lower ECE wins. Calibration matters because the gate acts on
           classification.category.prob (min_class_prob, ambiguity_margin).
  fallback the same rule over the TF-IDF candidates only: used when embeddings cannot run (offline,
           DOC_SEARCH_DENSE=0, low memory)
  priority compared as evidence only; a rule decides it (no model is good enough, see docs/EVALUATION.md)

Test is never read. Outputs:
  docs/ml/all-classifiers-val.json / .md         every candidate, for the presentation
  backend/app/tickets/classifier_choice.json     what classify.py trains and uses (committed: the deployed app
                                                 trains from it at startup, since backend/models/ is git-ignored)

Usage (from backend):  .\\.venv\\Scripts\\python.exe scripts\\compare_all_classifiers.py
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from sklearn.pipeline import Pipeline

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

import compare_classifiers as akh  # noqa: E402  (Akhilesh's script: candidates, folds, metrics, winner rule)
from app.analysis.stats import mcnemar  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import f1_score  # noqa: E402

from app.analysis.stats import brier  # noqa: E402
from app.tickets.classify import DECODERS, build_model  # noqa: E402
from app.tickets.clf_pipeline import CSV_PATH, LABELS, load_splits  # noqa: E402
from app.tickets.features import (EMBEDDINGS_NPZ, TEXT_RECIPE, StaleEmbeddingsError,  # noqa: E402
                                  load_embedding_cache)

DOCS_ML = BACKEND.parent / "docs" / "ml"
EMB_RESULTS = DOCS_ML / "embedding-models-val.json"
CHOICE_FILE = BACKEND / "app" / "tickets" / "classifier_choice.json"
OUT_NAME = "all-classifiers-val"
RULE_LABELS = ("priority",)
# Settings found by compare_embedding_models.py (GridSearchCV on train only), used if its results file is missing.
EMB_DEFAULTS = {"emb_lr": {"C": 1.0}, "emb_svm": {"C": 1.0}, "emb_rf": {"n_estimators": 500, "max_depth": None}}


# ---- Candidates: one spec per model, built the same way at comparison time and in classify.py -----------

def emb_settings(label: str, results_file: Path = EMB_RESULTS) -> dict[str, dict[str, Any]]:
    """The train-only tuned settings of the 3 embedding models for this label."""
    out = {k: dict(v) for k, v in EMB_DEFAULTS.items()}
    if results_file.exists():
        models = json.loads(results_file.read_text(encoding="utf-8"))["targets"].get(label, {}).get("models", {})
        if "logistic_regression" in models:
            out["emb_lr"] = {"C": models["logistic_regression"]["best_params"]["clf__C"]}
        if "linear_svm_calibrated" in models:
            out["emb_svm"] = {"C": models["linear_svm_calibrated"]["best_params"]["clf__estimator__C"]}
        if "random_forest" in models:
            out["emb_rf"] = dict(models["random_forest"]["best_params"])
    return out


def fit(spec: dict[str, Any], rows: Sequence[dict], y: Sequence[str], folds: Any, cache: Optional[dict] = None,
        rf_trees: Optional[int] = None) -> Pipeline:
    """Built by app.tickets.classify.build_model, the same code the app trains with. rf_trees: tests only."""
    if rf_trees and spec["name"] in ("rf", "emb_rf"):
        spec = {**spec, "params": {**spec["params"], "n_estimators": rf_trees}}
    model = build_model(spec, folds, cache)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*least populated class.*")
        warnings.filterwarnings("ignore", category=UserWarning, module="sklearn.model_selection")
        warnings.filterwarnings("ignore", message=".*did not converge.*")
        return model.fit(list(rows), list(y))


def all_specs(label: str, results_file: Path = EMB_RESULTS) -> list[dict[str, Any]]:
    specs = [{"kind": "tfidf", "name": c.name, "params": c.params, "by": "Akhilesh"} for c in akh.candidates()]
    specs += [{"kind": "embedding", "name": n, "params": p, "by": "Raswanth"}
              for n, p in emb_settings(label, results_file).items()]
    return specs


# ---- The comparison ----------------------------------------------------------------------------------

def run(csv_path: Path = CSV_PATH, npz_path: Path = EMBEDDINGS_NPZ, labels: Sequence[str] = LABELS,
        results_file: Path = EMB_RESULTS, rf_trees: Optional[int] = None,
        log: Callable[[str], None] = print) -> dict[str, Any]:
    data = load_splits(("train", "val"), csv_path)          # test is never loaded
    train, val = data["train"], data["val"]
    cache = load_embedding_cache(("train", "val"), csv_path, npz_path)
    report: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "split": "val", "test_used": False,
        "rows": {"train": len(train), "val": len(val)}, "tickets_csv_sha256": akh.sha256_of(csv_path),
        "text_recipe": TEXT_RECIPE, "selection": akh.SELECTION, "labels": {}}
    winners: dict[str, Any] = {}

    for label in labels:
        y_tr, y_va = [r[label] for r in train], [r[label] for r in val]
        folds = akh.group_folds(train, y_tr)
        specs = {s["name"]: s for s in all_specs(label, results_file)}
        results: dict[str, dict[str, Any]] = {}
        fitted: dict[str, Any] = {}
        for name, spec in specs.items():
            t0 = time.perf_counter()
            model = fit(spec, train, y_tr, folds, cache, rf_trees)
            secs = time.perf_counter() - t0
            fitted[name] = model
            results[name] = {**akh.evaluate(model, val, y_va), "fit_seconds": secs}
            log(f"  {label:12s} {spec['kind']:9s} {name:9s} macro-F1 {results[name]['macro_f1']:.3f}  "
                f"acc {results[name]['accuracy']:.3f}  ECE {results[name]['ece']:.3f}  ({secs:.1f} s)")

        winner, runner_up, reason = akh.pick_winner(results)
        tfidf_only = {k: v for k, v in results.items() if specs[k]["kind"] == "tfidf"}
        fallback, _, fallback_reason = akh.pick_winner(tfidf_only)
        leader = max(results, key=lambda k: (results[k]["macro_f1"], -results[k]["ece"]))
        w = results[winner]
        entry: dict[str, Any] = {
            "winner": winner, "winner_kind": specs[winner]["kind"], "runner_up": runner_up, "reason": reason,
            "highest_macro_f1": leader,
            "mcnemar_vs_runner_up": mcnemar(w["_correct"], results[runner_up]["_correct"]) if runner_up else None,
            "fallback_tfidf": fallback, "fallback_reason": fallback_reason,
            "mcnemar_winner_vs_fallback": (mcnemar(w["_correct"], results[fallback]["_correct"])
                                           if fallback != winner else None),
            "winner_macro_f1_ci95": [round(x, 4) for x in akh.bootstrap_macro_f1(y_va, w["_pred"])[1:]],
            "candidates": {k: {**akh._public(v), "kind": specs[k]["kind"], "by": specs[k]["by"],
                               "params": specs[k]["params"]} for k, v in results.items()},
        }
        _, _, entry["winner_top_confusions"] = akh.top_confusions(y_va, w["_pred"])
        entry["used_by_app"] = "rule" if label in RULE_LABELS else winner
        report["labels"][label] = entry
        winners[label] = fitted[winner]

    if {"category", "subcategory"} <= set(labels):
        report["decoding"] = decoding_eval(winners["category"], winners["subcategory"], train, val)
    return report


def decoding_eval(cat_model: Any, sub_model: Any, train: Sequence[dict], val: Sequence[dict]) -> dict[str, Any]:
    """How to combine the two winners so (category, subcategory) is always a valid pair: classify.DECODERS,
    the exact code the app runs, scored on val and chosen with the same rule."""
    parent = {r["subcategory"]: r["category"] for r in train}
    rows = list(val)   # whole rows: ticket_id lets an embedding model use the saved vectors (text reads only
    pc, ps = cat_model.predict_proba(rows), sub_model.predict_proba(rows)   # subject + description)
    cats, subs = [str(c) for c in cat_model.classes_], [str(s) for s in sub_model.classes_]
    yc, ys = [r["category"] for r in val], [r["subcategory"] for r in val]
    out: dict[str, dict[str, Any]] = {}
    for name, dec in DECODERS.items():
        c_pred, c_conf, s_pred = [], [], []
        for i in range(len(rows)):
            ranked, sub, _ = dec(pc[i], cats, ps[i], subs, parent)
            c_pred.append(ranked[0][0])
            c_conf.append(ranked[0][1])
            s_pred.append(sub)
        ok = np.array(c_pred, dtype=object) == np.array(yc, dtype=object)
        out[name] = {"accuracy": float(ok.mean()), "macro_f1": float(f1_score(yc, c_pred, average="macro",
                                                                                zero_division=0)),
                     "ece": akh.ece(c_conf, ok), "brier": brier(c_conf, ok),
                     "subcategory_accuracy": float(np.mean(np.array(s_pred, dtype=object) == np.array(ys, dtype=object))),
                     "subcategory_macro_f1": float(f1_score(ys, s_pred, average="macro", zero_division=0)),
                     "_correct": ok.tolist()}
    winner, other, reason = akh.pick_winner(out)
    return {"winner": winner, "reason": reason,
            "mcnemar_category": mcnemar(out[winner]["_correct"], out[other]["_correct"]),
            "candidates": {k: {m: round(v, 4) for m, v in r.items() if not m.startswith("_")} for k, r in out.items()}}


def choice(report: dict[str, Any], results_file: Path = EMB_RESULTS) -> dict[str, Any]:
    """The small file classify.py trains from (no model weights, just what to build)."""
    out: dict[str, Any] = {"created_at": report["created_at"], "tickets_csv_sha256": report["tickets_csv_sha256"],
                           "text_recipe": report["text_recipe"], "selection": report["selection"],
                           "source": "backend/scripts/compare_all_classifiers.py (val split)",
                           "decoding": report.get("decoding", {}).get("winner", "joint"), "labels": {}}
    for label, r in report["labels"].items():
        if label in RULE_LABELS:
            out["labels"][label] = {"use": "rule", "best_model_val_macro_f1": r["candidates"][r["winner"]]["macro_f1"]}
            continue
        specs = {s["name"]: s for s in all_specs(label, results_file)}
        pick = lambda name: {"kind": specs[name]["kind"], "name": name, "params": specs[name]["params"],  # noqa: E731
                             "val_macro_f1": r["candidates"][name]["macro_f1"], "val_ece": r["candidates"][name]["ece"]}
        out["labels"][label] = {"use": "model", "primary": pick(r["winner"]), "reason": r["reason"],
                                "fallback": pick(r["fallback_tfidf"])}
    return out


# ---- Report -------------------------------------------------------------------------------------------

def _p(mc: Optional[dict]) -> str:
    return "-" if not mc else f"p = {mc['p_value']:.3f} (b={mc['b']}, c={mc['c']})"


def markdown(report: dict[str, Any]) -> str:
    rows = report["rows"]
    lines = ["# All ticket classifiers on the validation split", "",
             f"Generated {report['created_at']} by `backend/scripts/compare_all_classifiers.py`. "
             f"Train {rows['train']}, val {rows['val']}. **Test was not used.**", "",
             "Nine candidates on identical footing: Akhilesh's 6 TF-IDF models and Raswanth's 3 embedding models "
             "(bge-small-en-v1.5), every one wrapped in sigmoid calibration over the same train folds (grouped by "
             "root_cause), scored on the same val tickets with the same code.", "",
             f"Winner rule (Akhilesh's, applied to all 9): {report['selection']}.", "",
             "| Label | Candidate | By | Features | Macro-F1 | Accuracy | ECE | Brier | Fit s |",
             "|---|---|---|---|---|---|---|---|---|"]
    for label, r in report["labels"].items():
        order = sorted(r["candidates"], key=lambda k: -r["candidates"][k]["macro_f1"])
        for name in order:
            m = r["candidates"][name]
            mark = " **(winner)**" if name == r["winner"] else (" (fallback)" if name == r["fallback_tfidf"] else "")
            lines.append(f"| {label} | {name}{mark} | {m['by']} | {m['kind']} | {m['macro_f1']:.3f} | "
                         f"{m['accuracy']:.3f} | {m['ece']:.3f} | {m['brier']:.3f} | {m['fit_seconds']:.1f} |")
    lines += ["", "| Label | Used by the app | Why | Winner macro-F1 (95% CI) | vs runner-up | "
              "TF-IDF fallback | Winner vs fallback |", "|---|---|---|---|---|---|---|"]
    for label, r in report["labels"].items():
        lo, hi = r["winner_macro_f1_ci95"]
        used = "rule (no model good enough)" if r["used_by_app"] == "rule" else r["winner"]
        lines.append(f"| {label} | {used} | {r['reason']} | {r['candidates'][r['winner']]['macro_f1']:.3f} "
                     f"({lo:.3f}-{hi:.3f}) | {r['runner_up']}: {_p(r['mcnemar_vs_runner_up'])} | "
                     f"{r['fallback_tfidf']} | {_p(r['mcnemar_winner_vs_fallback'])} |")
    dec = report.get("decoding")
    if dec:
        mc = dec["mcnemar_category"]
        lines += ["", "## Combining the two winners (what the app runs)", "",
                  "Every subcategory belongs to exactly one category, so the category and subcategory winners are "
                  "combined over valid pairs only. Category-first: the category model decides and the subcategory "
                  "must belong to it. Joint: p_category x p_subcategory over valid pairs, category confidence = its "
                  "share of that joint probability (`classify.decode`). Same val tickets, same winner rule.", "",
                  "| Decoding | Category accuracy | Category macro-F1 | Category ECE | Category Brier | "
                  "Subcategory accuracy | Subcategory macro-F1 |", "|---|---|---|---|---|---|---|"]
        for name, m in dec["candidates"].items():
            mark = " **(used)**" if name == dec["winner"] else ""
            lines.append(f"| {name}{mark} | {m['accuracy']:.3f} | {m['macro_f1']:.3f} | {m['ece']:.3f} | "
                         f"{m['brier']:.3f} | {m['subcategory_accuracy']:.3f} | {m['subcategory_macro_f1']:.3f} |")
        lines += ["", f"Why: {dec['reason']}. McNemar on category, {dec['winner']} vs the other: {_p(mc)}."]
    for label, r in report["labels"].items():
        if r["used_by_app"] != "rule":
            lines += ["", f"Top {label} confusions of the winner on val (true -> predicted, count):", ""]
            lines += [f"- {c['true']} -> {c['predicted']}: {c['count']}" for c in r["winner_top_confusions"]]
    return "\n".join(lines) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    argparse.ArgumentParser(description="Compare all classifier candidates on val; write the app's choice.").parse_args(argv)
    try:
        report = run()
    except StaleEmbeddingsError as exc:
        print(f"STOPPED: {exc}")
        return 1
    DOCS_ML.mkdir(parents=True, exist_ok=True)
    (DOCS_ML / f"{OUT_NAME}.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (DOCS_ML / f"{OUT_NAME}.md").write_text(markdown(report), encoding="utf-8")
    CHOICE_FILE.write_text(json.dumps(choice(report), indent=2) + "\n", encoding="utf-8")
    print()
    print(markdown(report))
    print(f"Saved docs/ml/{OUT_NAME}.json/.md and {CHOICE_FILE.relative_to(BACKEND.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
