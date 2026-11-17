"""Step 1b: classify a ticket (FR-01): category, subcategory, priority, each with a confidence.

Which model is used comes from classifier_choice.json, written by scripts/compare_all_classifiers.py after comparing
all 9 candidates (Akhilesh's 6 TF-IDF + Raswanth's 3 embedding models) on the val split with one rule.

  category +   the two chosen models combined over VALID (category, subcategory) pairs only (decode(): each
  subcategory subcategory belongs to one category; joint = p_cat x p_sub, normalised). category.prob = the
               category's share of that joint probability; top3_category for the gate's ambiguity check.
               subcategory.prob = the subcategory model's own probability.
  priority     a RULE, not a model (no model beat the majority on val; see docs/EVALUATION.md): the reporter's
               impact, raised to at least P2 for production outages. Classification.priority_rule names the rule;
               the probability is 0.0 because no model is behind it.

Models are trained from tickets.csv (train split) when the app starts (load_classifier, a warm-up), because
backend/models/ is git-ignored and never deployed; they are cached there locally. If an embedding model cannot
run (offline, low memory), that label falls back to the chosen TF-IDF model.

Runs on the RAW ticket text, in memory only (mentor's order): nothing here logs, stores or returns ticket text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
from collections import defaultdict
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

import joblib
import numpy as np
import sklearn
import yaml
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

from app.common.paths import BACKEND_DIR
from app.tickets.clf_pipeline import CSV_PATH, load_splits, make_pipeline
from app.tickets.features import TEXT_RECIPE, EmbedRows, StaleEmbeddingsError, load_embedding_cache
from app.tickets.models import Classification, LabelScore, TicketIn

logger = logging.getLogger(__name__)

CHOICE_FILE = Path(__file__).with_name("classifier_choice.json")
RULES_FILE = Path(__file__).with_name("classify.yaml")
MODELS_DIR = BACKEND_DIR / "models"
MODEL_LABELS = ("category", "subcategory")
TFIDF_SEED = 0      # compare_classifiers.py's SEED: the TF-IDF models are built exactly as compared
EMB_SEED = 42       # compare_embedding_models.py's SEED


# ---- Building the compared models (the comparison script uses these too, so app = what was compared) -------

def build_estimator(spec: dict[str, Any]) -> tuple[Any, bool]:
    """(unfitted estimator, needs scaling) for {"kind", "name", "params"} from classifier_choice.json."""
    kind, name, p = spec["kind"], spec["name"], dict(spec["params"])
    if kind == "tfidf":
        base = name.split("_")[0]
        if base == "nb":
            return MultinomialNB(**p), False
        if base == "lr":
            return LogisticRegression(**p, max_iter=3000), False
        if base == "svm":
            return LinearSVC(**p, random_state=TFIDF_SEED), False
        if base == "rf":
            return RandomForestClassifier(**p, n_jobs=-1, random_state=TFIDF_SEED), False
    elif kind == "embedding":
        if name == "emb_lr":
            return LogisticRegression(C=p["C"], max_iter=5000, random_state=EMB_SEED), True
        if name == "emb_svm":
            return LinearSVC(C=p["C"], max_iter=10000, random_state=EMB_SEED), True
        if name == "emb_rf":
            return RandomForestClassifier(n_estimators=p["n_estimators"], max_depth=p["max_depth"],
                                          random_state=EMB_SEED, n_jobs=-1), False
    raise ValueError(f"unknown model {kind}/{name}")


def build_model(spec: dict[str, Any], folds: Any, cache: Optional[dict] = None) -> Pipeline:
    """Unfitted pipeline, rows in: text -> TF-IDF or embeddings -> sigmoid-calibrated classifier."""
    est, scale = build_estimator(spec)
    calibrated = CalibratedClassifierCV(est, method="sigmoid", cv=folds)
    if spec["kind"] == "tfidf":
        return make_pipeline(calibrated)
    steps: list[tuple[str, Any]] = [("embed", EmbedRows(cache))]
    if scale:
        steps.append(("scale", StandardScaler()))
    steps.append(("clf", calibrated))
    return Pipeline(steps)


def calibration_folds(rows: Sequence[dict], y: Sequence[str], n_splits: int = 3) -> list:
    """Same folds as compare_classifiers.group_folds: over train, tickets with the same root_cause stay together.
    root_cause is a grouping id only, never a feature."""
    groups = [r.get("root_cause") or f"_row{i}" for i, r in enumerate(rows)]
    sgkf = StratifiedGroupKFold(n_splits=n_splits, shuffle=True, random_state=TFIDF_SEED)
    return list(sgkf.split(np.zeros(len(y)), list(y), groups))


# ---- Loading / training -------------------------------------------------------------------------------

def _choice(path: Path = CHOICE_FILE) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _rules() -> dict[str, Any]:
    with RULES_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class _State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.models: dict[str, Any] = {}          # label -> fitted pipeline in use
        self.names: dict[str, str] = {}           # label -> "embedding/emb_lr" etc.
        self.fallback_specs: dict[str, dict] = {}
        self.valid_subs: dict[str, set[str]] = {}
        self.train_rows: list[dict] = []
        self.folds: dict[str, list] = {}
        self.csv_path: Path = CSV_PATH
        self.models_dir: Path = MODELS_DIR
        self.key = ""
        self.decoding = "joint"


_STATE = _State()


def embeddings_enabled() -> bool:
    """Same switch as search: DOC_SEARCH_DENSE=0 (tests, low memory) turns embeddings off -> TF-IDF fallback."""
    return os.getenv("DOC_SEARCH_DENSE", "1").strip().lower() not in ("0", "false", "no", "off")


def _fit(spec: dict[str, Any], label: str, st: _State) -> Any:
    if spec["kind"] == "embedding" and not embeddings_enabled():
        raise RuntimeError("embeddings are switched off (DOC_SEARCH_DENSE=0)")
    y = [r[label] for r in st.train_rows]
    folds = st.folds.setdefault(label, calibration_folds(st.train_rows, y))
    cache = None
    if spec["kind"] == "embedding":
        try:
            cache = load_embedding_cache(("train",), st.csv_path)
        except StaleEmbeddingsError:
            cache = None    # no saved vectors (e.g. on the server): EmbedRows embeds the train rows itself
    model = build_model(spec, folds, cache).fit(list(st.train_rows), y)
    if spec["kind"] == "embedding":
        model.named_steps["embed"].cache = None   # live tickets are embedded; don't keep training vectors
    return model


def _cache_path(st: _State, label: str) -> Path:
    return st.models_dir / f"classify_{label}.joblib"


def load_classifier(choice_path: Path = CHOICE_FILE, csv_path: Path = CSV_PATH, models_dir: Path = MODELS_DIR,
                    force: bool = False) -> dict[str, str]:
    """Train (or load cached) models for category and subcategory. Startup warm-up; safe to call again.
    Returns {label: "kind/name"} actually in use."""
    choice = _choice(choice_path)
    key = hashlib.sha256(json.dumps([choice, _sha256(csv_path), TEXT_RECIPE, sklearn.__version__],
                                    sort_keys=True).encode()).hexdigest()[:16]
    with _STATE.lock:
        if _STATE.key == key and _STATE.models and not force:
            return dict(_STATE.names)
        st = _State()
        st.csv_path, st.models_dir, st.key = Path(csv_path), Path(models_dir), key
        st.decoding = choice.get("decoding", "joint")
        if st.decoding not in DECODERS:
            raise ValueError(f"unknown decoding {st.decoding!r} in {choice_path}")
        st.train_rows = load_splits(("train",), csv_path)["train"]
        for r in st.train_rows:
            st.valid_subs.setdefault(r["category"], set()).add(r["subcategory"])
        for label in MODEL_LABELS:
            entry = choice["labels"][label]
            st.fallback_specs[label] = entry["fallback"]
            path = _cache_path(st, label)
            cached = None
            if path.exists() and not force:
                try:
                    cached = joblib.load(path)
                    if cached.get("key") != key:
                        cached = None
                except Exception:
                    cached = None
            if cached:
                st.models[label], st.names[label] = cached["model"], cached["name"]
                continue
            for spec in (entry["primary"], entry["fallback"]):
                try:
                    st.models[label] = _fit(spec, label, st)
                    st.names[label] = f"{spec['kind']}/{spec['name']}"
                    break
                except Exception as exc:  # e.g. the embedding model cannot load: try the TF-IDF fallback
                    logger.warning("classifier %s: %s/%s unavailable (%s); trying fallback", label, spec["kind"],
                                   spec["name"], type(exc).__name__)
            else:
                raise RuntimeError(f"no classifier could be trained for {label}")
            try:
                st.models_dir.mkdir(parents=True, exist_ok=True)
                joblib.dump({"key": key, "name": st.names[label], "model": st.models[label]}, path)
            except OSError as exc:   # a read-only disk only costs a retrain next start
                logger.warning("classifier %s: not cached (%s)", label, type(exc).__name__)
        _STATE.__dict__.update({k: v for k, v in st.__dict__.items() if k != "lock"})
        logger.info("classifier ready: %s", _STATE.names)
        return dict(_STATE.names)


def _proba(label: str, row: dict[str, Any]) -> tuple[list[str], np.ndarray]:
    """Class names and probabilities; if the model in use fails (e.g. embeddings went away), switch this label
    to its TF-IDF fallback for good and use that."""
    model = _STATE.models[label]
    try:
        return [str(c) for c in model.classes_], model.predict_proba([row])[0]
    except Exception as exc:
        spec = _STATE.fallback_specs[label]
        if _STATE.names[label] == f"{spec['kind']}/{spec['name']}":
            raise
        logger.warning("classifier %s failed (%s); switching to %s", label, type(exc).__name__, spec["name"])
        with _STATE.lock:
            _STATE.models[label] = _fit(spec, label, _STATE)
            _STATE.names[label] = f"{spec['kind']}/{spec['name']}"
        model = _STATE.models[label]
        return [str(c) for c in model.classes_], model.predict_proba([row])[0]


# ---- Priority rule -------------------------------------------------------------------------------------

_RANK = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}


def priority_by_rule(ticket: TicketIn) -> tuple[str, str]:
    """(priority, rule code). impact -> priority; no impact -> the default; a production outage is raised to
    at least outage_bump.to. Numbers and words live in classify.yaml."""
    r = _rules()["priority"]
    if ticket.impact:
        label, code = r["impact"][ticket.impact], f"IMPACT_{ticket.impact.upper()}"
    else:
        label, code = r["default"], f"DEFAULT_{r['default']}"
    bump = r["outage_bump"]
    env = (ticket.environment or "").strip().lower()
    text = f"{ticket.subject} {ticket.description}".lower()
    if env in bump["environments"] and any(re.search(rf"\b{re.escape(w)}\b", text) for w in bump["words"]):
        if _RANK[bump["to"]] < _RANK[label]:
            label, code = bump["to"], "OUTAGE_BUMP"
    return label, code


# ---- Combining the two models ----------------------------------------------------------------------------

def decode(p_cat: np.ndarray, cats: Sequence[str], p_sub: np.ndarray, subs: Sequence[str],
           parent: dict[str, str]) -> tuple[list[tuple[str, float]], str, float]:
    """Joint decoding over VALID pairs only. Every subcategory belongs to exactly one category (parent), so:
        joint(s)        = p_cat(parent(s)) * p_sub(s), normalised over all valid pairs
        category mass   = sum of joint(s) over that category's subcategories
    Returns (categories by mass, best first, as (label, mass); best subcategory of the top category; its p_sub).
    On val this beat "category model first" (category accuracy 0.795 -> 0.892, ECE 0.191 -> ~0.05), because the
    stronger subcategory model can correct the category model instead of being forced into its mistakes."""
    cat_ix = {c: i for i, c in enumerate(cats)}
    pairs = [(j, s) for j, s in enumerate(subs) if parent.get(s) in cat_ix]
    if not pairs:   # nothing consistent: fall back to each model on its own
        order = np.argsort(-p_cat)
        j = int(np.argmax(p_sub))
        return [(cats[i], float(p_cat[i])) for i in order], subs[j], float(p_sub[j])
    joint = np.array([p_cat[cat_ix[parent[s]]] * p_sub[j] for j, s in pairs])
    total = joint.sum()
    joint = joint / total if total > 0 else np.full(len(pairs), 1.0 / len(pairs))
    mass: dict[str, float] = {c: 0.0 for c in cats}
    for (j, s), q in zip(pairs, joint):
        mass[parent[s]] += float(q)
    ranked = sorted(mass.items(), key=lambda kv: (-kv[1], kv[0]))
    top = ranked[0][0]
    j_best, s_best = max(((j, s) for (j, s), q in zip(pairs, joint) if parent[s] == top), key=lambda js: p_sub[js[0]])
    return ranked, s_best, float(p_sub[j_best])


def decode_category_first(p_cat: np.ndarray, cats: Sequence[str], p_sub: np.ndarray, subs: Sequence[str],
                          parent: dict[str, str]) -> tuple[list[tuple[str, float]], str, float]:
    """The simpler alternative (kept for comparison): category model decides, subcategory is the best one of
    that category."""
    order = np.argsort(-p_cat)
    top = cats[order[0]]
    idx = [j for j, s in enumerate(subs) if parent.get(s) == top] or list(range(len(subs)))
    j = max(idx, key=lambda k: p_sub[k])
    return [(cats[i], float(p_cat[i])) for i in order], subs[j], float(p_sub[j])


DECODERS = {"joint": decode, "category_first": decode_category_first}


# ---- classify ----------------------------------------------------------------------------------------

def classify(ticket: TicketIn) -> Classification:
    """category + subcategory from the chosen models (joint decoding), priority from the rule.
    Raw text stays in memory."""
    if not _STATE.models:
        load_classifier()
    row = {"subject": ticket.subject, "description": ticket.description}
    cats, p_cat = _proba("category", row)
    subs, p_sub = _proba("subcategory", row)
    parent = {s: c for c, ss in _STATE.valid_subs.items() for s in ss}
    ranked, sub_label, sub_prob = DECODERS[_STATE.decoding](p_cat, cats, p_sub, subs, parent)

    category = LabelScore(label=ranked[0][0], prob=round(ranked[0][1], 4))
    top3 = [LabelScore(label=c, prob=round(m, 4)) for c, m in ranked[:3]]
    subcategory = LabelScore(label=sub_label, prob=round(sub_prob, 4))   # the subcategory model's own probability

    prio, rule = priority_by_rule(ticket)
    names = _STATE.names
    return Classification(
        category=category, subcategory=subcategory, priority=LabelScore(label=prio, prob=0.0),
        top3_category=top3, key_terms=[], priority_rule=rule,
        model_version=f"cat:{names['category']} sub:{names['subcategory']} {date.today().isoformat()}")
