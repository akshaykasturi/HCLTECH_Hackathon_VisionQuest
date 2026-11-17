"""compare_classifiers (FR-01): every candidate trains on a tiny made-up set, the winner rule, and test isolation."""
import csv
import json

import numpy as np
import pytest

import scripts.compare_classifiers as cc
from app.tickets import clf_pipeline as cp

WORDS = {"Network": "vpn tunnel gateway wifi", "Email": "outlook mailbox inbox smtp",
         "Hardware": "laptop battery monitor keyboard"}
COLS = ["ticket_id", "subject", "description", "category", "subcategory", "priority", "root_cause", "split"]


def _rows(split, per_cause=3, causes=4, label=None):
    rows = []
    for cat, words in WORDS.items():
        w = words.split()
        for c in range(causes):
            for i in range(per_cause):
                rows.append({"ticket_id": f"{split}-{cat}-{c}-{i}", "subject": f"{w[c % 4]} problem {i}",
                             "description": f"{w[(c + i) % 4]} {w[(c + 1) % 4]} not working",
                             "category": label or cat, "subcategory": label or f"{cat}-sub",
                             "priority": label or ("P1" if c == 0 else "P3"),
                             "root_cause": f"{split}-{cat}-cause-{c}", "split": split})
    return rows


@pytest.fixture
def tiny_csv(tmp_path):
    path = tmp_path / "tickets.csv"
    # Test rows carry a POISON label: if the default run read them, POISON would show up somewhere.
    rows = _rows("train") + _rows("val", per_cause=1, causes=2) + _rows("test", per_cause=1, causes=2, label="POISON")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def test_every_candidate_trains_and_gives_probabilities(tiny_csv):
    data = cp.load_splits(("train", "val"), tiny_csv)
    y = [r["category"] for r in data["train"]]
    folds = cc.group_folds(data["train"], y)
    for cand in cc.candidates(rf_trees=20):
        model = cc.fit(cand, data["train"], y, folds)
        proba = model.predict_proba(data["val"])
        assert proba.shape == (len(data["val"]), 3), cand.name
        assert np.allclose(proba.sum(axis=1), 1.0), cand.name


def test_calibration_folds_keep_a_root_cause_together(tiny_csv):
    train = cp.load_splits(("train",), tiny_csv)["train"]
    for _, held in cc.group_folds(train, [r["category"] for r in train]):
        held_causes = {train[i]["root_cause"] for i in held}
        others = {train[i]["root_cause"] for i in range(len(train)) if i not in set(held)}
        assert not held_causes & others


N = 100
SAME = [True] * N                                    # identical answers: McNemar p = 1.0 (lead not significant)
FAR = [True] * N                                     # vs WRONG30: 30 disagreements all one way, p << 0.05
WRONG30 = [False] * 30 + [True] * 70


def _res(f1, e, correct=SAME):
    return {"macro_f1": f1, "ece": e, "_correct": list(correct)}


def _pick(results):
    w, r, _ = cc.pick_winner(results)
    return w, r


def test_winner_is_best_macro_f1_when_lead_is_significant():
    # b is far better calibrated, but a's lead is significant: a stays.
    assert _pick({"a": _res(0.80, 0.30, FAR), "b": _res(0.70, 0.01, WRONG30)}) == ("a", "b")


def test_winner_is_best_macro_f1_when_no_one_is_much_better_calibrated():
    # Not significant, but the ECE gap is only 0.04 (< 0.05): a stays.
    assert _pick({"a": _res(0.80, 0.30), "b": _res(0.70, 0.26)}) == ("a", "b")


def test_calibration_rule_not_significant_and_ece_005_lower():
    # a leads, not significantly; b and c are both >= 0.05 better calibrated: the higher macro-F1 of them (b) wins.
    w, r, why = cc.pick_winner({"a": _res(0.83, 0.27), "b": _res(0.77, 0.19), "c": _res(0.70, 0.10),
                                "d": _res(0.60, 0.25)})
    assert (w, r) == ("b", "a")
    assert why.startswith("calibration rule")


def test_calibration_rule_skips_a_candidate_the_leader_beats_significantly():
    # b is better calibrated but a beats it significantly; c is not beaten significantly: c wins.
    assert _pick({"a": _res(0.83, 0.30, FAR), "b": _res(0.80, 0.10, WRONG30), "c": _res(0.75, 0.20, FAR)}) == ("c", "a")


def test_within_001_the_lower_ece_wins():
    assert _pick({"a": _res(0.805, 0.20), "b": _res(0.800, 0.17), "c": _res(0.5, 0.18)}) == ("b", "a")


def test_within_001_but_higher_ece_keeps_the_leader():
    assert _pick({"a": _res(0.805, 0.05), "b": _res(0.800, 0.20)}) == ("a", "b")


def test_priority_purity_majority_and_lookup():
    train = [{"priority": "P3", "environment": "prod"}] * 3 + [{"priority": "P1", "environment": "dev"}]
    val = [{"priority": "P1", "environment": "dev"}, {"priority": "P3", "environment": "new"}]
    pur = cc.priority_purity(train, val, columns=("environment",))
    assert pur["majority"]["label"] == "P3" and pur["majority"]["train_share"] == 0.75
    env = pur["columns"]["environment"]
    assert env["train_purity"] == 1.0
    assert env["val_accuracy"] == 1.0 and env["val_p1_recall"] == 1.0   # dev -> P1; unseen "new" -> P3


def test_ece_and_bootstrap():
    assert cc.ece([0.9, 0.9], [True, True]) == pytest.approx(0.1)
    assert cc.ece([1.0, 0.0], [True, False]) == pytest.approx(0.0)
    point, lo, hi = cc.bootstrap_macro_f1(["a", "b", "a", "b"], ["a", "b", "b", "b"], n=200)
    assert lo <= point <= hi


def test_default_run_never_touches_test(tiny_csv, tmp_path, monkeypatch):
    asked = []
    real = cc.load_splits

    def spy(splits, csv_path):
        asked.append(tuple(splits))
        return real(splits, csv_path)

    monkeypatch.setattr(cc, "load_splits", spy)
    models = tmp_path / "models"
    report = cc.run(tiny_csv, models_dir=models, labels=("category", "priority"), rf_trees=20,
                    results_dir=tmp_path / "results", n_boot=50, log=lambda _: None)

    assert asked and all("test" not in s for s in asked)
    assert "POISON" not in json.dumps(report)
    manifest = json.loads((models / "clf_manifest.json").read_text())
    assert "final" not in manifest and manifest["text_recipe"] == cp.TEXT_RECIPE
    assert (models / "clf_category.joblib").exists()
    # Priority: compared as evidence, but no model saved; the manifest says a rule decides.
    assert not (models / "clf_priority.joblib").exists()
    assert manifest["labels"]["priority"] == cc.PRIORITY_RULE
    assert "P1" in report["labels"]["priority"]["val"]["recall"]
    assert set(report["priority_purity"]["columns"]) == set(cc.PURITY_COLUMNS)
    md = cc.val_markdown(report)
    assert "Selection on val; test reported once with --final" in md
    assert cc.HONESTY_NOTE in md and md.index(cc.HONESTY_NOTE) < md.index("## Classifier comparison (val)")
    assert "rule was added after seeing val results, before any final test scoring" in md
    assert "### Priority: why a rule, not a model" in md

    # --final reads test once (saved models only, not priority), then refuses a second time.
    final = cc.run_final(tiny_csv, models_dir=models)
    assert set(final["labels"]) == {"category"}
    with pytest.raises(cc.FinalRefused):
        cc.run_final(tiny_csv, models_dir=models)


def test_write_section_replaces_only_its_own_block(tmp_path):
    md = tmp_path / "EVALUATION.md"
    md.write_text("# Evaluation\n\nOther section stays.\n")
    cc.write_section(md, cc.VAL_START, cc.VAL_END, f"{cc.VAL_START}\nfirst\n{cc.VAL_END}")
    cc.write_section(md, cc.VAL_START, cc.VAL_END, f"{cc.VAL_START}\nsecond\n{cc.VAL_END}")
    text = md.read_text()
    assert "Other section stays." in text and "second" in text and "first" not in text
