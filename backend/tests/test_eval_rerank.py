"""Smoke test for scripts/eval_rerank.py on a tiny made-up dataset (BM25 only, see conftest.py)."""
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_rerank.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("eval_rerank", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(tid, created, group, split, words):
    return {"ticket_id": tid, "created_at": created, "resolved_at": created, "status": "resolved",
            "category": "Network", "subcategory": "VPN", "affected_service": "VPN", "subject": words,
            "description": words, "root_cause": "x", "resolution_notes": "Fixed.", "resolution_steps": "a | b",
            "dup_group_id": group, "split": split}


ROWS = [
    _row("A1", "2026-01-01T00:00:00+00:00", "g1", "val", "vpn error 809 tunnel"),
    _row("A2", "2026-01-02T00:00:00+00:00", "g1", "val", "vpn error 809 tunnel again"),
    _row("A3", "2026-01-05T00:00:00+00:00", "g1", "val", "vpn tunnel error 809 laptop"),   # the query
    _row("B1", "2026-01-03T00:00:00+00:00", "g2", "train", "printer jammed paper tray"),
    _row("B2", "2026-01-04T00:00:00+00:00", "g3", "train", "mailbox full quota"),
]


def test_rerank_eval_runs_and_is_time_aware(ev, tmp_path):
    (tmp_path / "labelled_cases.jsonl").write_text("\n".join(json.dumps(c) for c in [
        {"ticket_id": "A3", "split": "val", "expected_category": "Network", "gold_similar_ticket_ids": ["A1", "A2"]},
        {"ticket_id": "A1", "split": "val", "expected_category": "Network", "gold_similar_ticket_ids": ["A2", "A3"]},
    ]), encoding="utf-8")
    (tmp_path / "retrieval_pairs.csv").write_text(
        "query_ticket_id,candidate_ticket_id,relevance,split,same_service,lookalike\nA3,B1,0,val,True,False\n",
        encoding="utf-8")
    res = ev.rerank_eval("val", pool_k=4, top_n=2, rows=ROWS, data_dir=tmp_path)
    # A1 has no earlier gold ticket (A2, A3 are in its future): skipped when time-aware.
    assert res["n"] == 1 and res["skipped"] == 1
    assert set(res["arms"]) == {"retrieval", "prior", "entropy", "blended", "ahp", "ahp_entropy"}
    assert res["ahp"]["accepted"] and res["ahp"]["cr"] < 0.10
    assert res["arms"]["retrieval"]["recall"] == [1.0]   # A1 and A2 are the two best text matches
    for arm in res["arms"].values():
        assert all(0.0 <= v <= 1.0 for key in ("recall", "mrr", "ndcg") for v in arm[key])

    future = ev.rerank_eval("val", pool_k=4, top_n=2, rows=ROWS, data_dir=tmp_path, time_aware=False)
    assert future["n"] == 2 and future["skipped"] == 0


def test_ndcg_by_hand(ev):
    # gains {x: 2, y: 1}; ranking [y, x]: DCG = 1/log2(2) + 3/log2(3); ideal [x, y]: 3/1 + 1/log2(3)
    import math
    expected = (1 + 3 / math.log2(3)) / (3 + 1 / math.log2(3))
    assert ev._ndcg(["y", "x"], {"x": 2, "y": 1}, k=2) == pytest.approx(expected)
    assert ev._ndcg(["z"], {}, k=5) == 0.0


def test_main_without_data_exits_cleanly(ev, monkeypatch, tmp_path, capsys):
    import app.common.paths as paths
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    assert ev.main(["--bm25-only"]) == 0
    assert "Nothing to evaluate" in capsys.readouterr().out
