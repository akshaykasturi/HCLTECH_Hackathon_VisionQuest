"""Classifier text features (app/tickets/features.py) and scripts/embed_tickets.py, offline with a fake embedder."""
import csv
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from app.common.doc_search import tokenize
from app.common.embed import l2_normalise
from app.tickets.features import LEAKY_COLUMNS, TEXT_COLUMNS, row_text, ticket_text

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "embed_tickets.py"
DIM = 32


@pytest.fixture(scope="module")
def emb():
    spec = importlib.util.spec_from_file_location("embed_tickets", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEmbed:
    """Deterministic bag-of-words vectors, unit length like the real model (same idea as test_doc_search.py)."""

    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, texts: list[str]) -> np.ndarray:
        self.seen.extend(texts)
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                out[row, int(hashlib.md5(token.encode()).hexdigest(), 16) % DIM] += 1.0
        return l2_normalise(out)


COLUMNS = ["ticket_id", "created_at", "resolved_at", "status", "channel", "category", "subcategory", "priority",
           "assigned_team", "affected_service", "environment", "subject", "description", "root_cause",
           "resolution_notes", "resolution_steps", "problem_id", "dup_group_id", "split"]


def _row(tid, split, category, subject, description):
    row = {c: f"LEAK_{c}_{tid}" for c in COLUMNS}   # every other column holds a marker that must not leak
    row.update(ticket_id=tid, split=split, category=category, subject=subject, description=description)
    return row


ROWS = [
    _row("T1", "train", "Network", "VPN error 809", "Tunnel will not connect"),
    _row("T2", "train", "Endpoint", "Printer jammed", "Paper stuck in tray 2"),
    _row("V1", "val", "Network", "VPN 809 again", "Cannot connect tunnel"),
    _row("V2", "val", "Endpoint", "Printer paper jam", "Tray 2 stuck"),
    _row("X1", "test", "Network", "VPN broken", "Error 809"),
]


def _write_csv(path: Path, rows) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return path


# ---- features.py -------------------------------------------------------------------------------------

def test_ticket_text_joins_and_collapses_whitespace():
    assert ticket_text("  VPN   error\t809 ", "Tunnel\n\nwill  not connect ") == "VPN error 809. Tunnel will not connect"


def test_ticket_text_with_a_missing_part():
    assert ticket_text("VPN down", "") == "VPN down"
    assert ticket_text(None, "only a description") == "only a description"
    assert ticket_text(None, None) == ""


def test_leaky_columns_are_the_agreed_list_and_not_text_inputs():
    assert LEAKY_COLUMNS == {"root_cause", "resolution_notes", "resolution_steps", "resolved_at", "status",
                             "category", "subcategory", "priority", "assigned_team", "problem_id", "dup_group_id"}
    assert not LEAKY_COLUMNS & set(TEXT_COLUMNS)


def test_leaky_columns_never_reach_the_text():
    for row in ROWS:
        text = row_text(row)
        assert "LEAK_" not in text
        assert text == ticket_text(row["subject"], row["description"])


# ---- scripts/embed_tickets.py ------------------------------------------------------------------------

def test_saved_files_have_the_right_shapes_and_meta(emb, tmp_path):
    csv_path = _write_csv(tmp_path / "tickets.csv", ROWS)
    fake = FakeEmbed()
    res = emb.run(csv_path, tmp_path / "processed", embed=fake, model_name="fake")

    data = np.load(res["npz"])
    assert data["vectors"].shape == (5, DIM) and data["vectors"].dtype == np.float32
    assert list(data["ticket_id"]) == ["T1", "T2", "V1", "V2", "X1"]
    assert list(data["split"]) == ["train", "train", "val", "val", "test"]

    meta = json.loads(res["meta"].read_text(encoding="utf-8"))
    assert meta["model"] == "fake" and meta["dim"] == DIM and meta["rows"] == 5
    assert meta["text_recipe"].startswith("v1")
    assert meta["tickets_csv_sha256"] == hashlib.sha256(csv_path.read_bytes()).hexdigest()
    assert meta["splits"] == {"train": 2, "val": 2, "test": 1}
    assert "created_at" in meta

    assert not any("LEAK_" in t for t in fake.seen)   # what was actually sent to the embedder
    assert res["splits"] == {"train": 2, "val": 2, "test": 1}


def test_val_nearest_neighbour_accuracy_never_uses_test(emb, tmp_path):
    csv_path = _write_csv(tmp_path / "tickets.csv", ROWS)
    res = emb.run(csv_path, tmp_path / "out", embed=FakeEmbed(), model_name="fake")
    assert res["val_1nn_category_accuracy"] == 1.0   # each val ticket's closest train ticket is its twin

    # A test ticket identical to a val ticket, with a different category, must not change the answer.
    vectors = np.eye(3, dtype=np.float32)[[0, 1, 0, 1, 0]]
    acc = emb.nn_accuracy(vectors, ["train", "train", "val", "val", "test"], ["A", "B", "A", "B", "Z"])
    assert acc == 1.0


def test_check_fails_on_nan(emb, tmp_path):
    def nan_embed(texts):
        out = FakeEmbed()(texts)
        out[1, 0] = np.nan
        return out
    csv_path = _write_csv(tmp_path / "tickets.csv", ROWS)
    with pytest.raises(emb.EmbeddingCheckError, match="NaN"):
        emb.run(csv_path, tmp_path / "out", embed=nan_embed, model_name="fake")
    assert not (tmp_path / "out").exists()   # nothing saved


def test_check_fails_on_duplicate_ticket_id(emb, tmp_path):
    csv_path = _write_csv(tmp_path / "tickets.csv", ROWS + [dict(ROWS[0])])
    with pytest.raises(emb.EmbeddingCheckError, match="duplicate ticket_id"):
        emb.run(csv_path, tmp_path / "out", embed=FakeEmbed(), model_name="fake")


def test_check_fails_on_wrong_row_count_and_non_unit_rows(emb):
    ids = ["a", "b"]
    with pytest.raises(emb.EmbeddingCheckError, match="expected 3 rows"):
        emb.check(np.ones((2, 4), dtype=np.float32) / 2, ids, 3)
    with pytest.raises(emb.EmbeddingCheckError, match="unit length"):
        emb.check(np.ones((2, 4), dtype=np.float32), ids, 2)   # norm 2, not 1


def test_main_reports_a_failed_check_and_saves_nothing(emb, tmp_path, monkeypatch, capsys):
    csv_path = _write_csv(tmp_path / "tickets.csv", ROWS + [dict(ROWS[0])])
    monkeypatch.setattr(emb, "embed_rows", lambda rows, embed=None, batch_size=64: (FakeEmbed()([row_text(r) for r in rows]), 0.0))
    assert emb.main(["--csv", str(csv_path), "--out-dir", str(tmp_path / "out")]) == 1
    assert "STOPPED, nothing saved" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()
