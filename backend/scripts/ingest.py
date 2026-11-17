"""Run the raw-to-clean ingestion pipeline (app/ingest/pipeline.py) on a raw ITSM export.

From backend:
    .\\.venv\\Scripts\\python.exe scripts\\ingest.py              # writes data/processed/ (git-ignored)
    .\\.venv\\Scripts\\python.exe scripts\\ingest.py --evaluate   # + scores every decision against the ground truth

Outputs (data/processed/):
    clean_tickets.csv       ACCEPT / NORMALIZE records: the curated field names + flags (text unmasked, as in
                            the curated layer; mask before search, storage, logs or display)
    duplicates.csv          MERGE_DUPLICATE: source_record_id -> canonical source_record_id
    quarantine.csv          QUARANTINE: source_record_id + reason codes (no ticket text)
    security_hold.csv       HOLD_SECURITY: source_record_id + screen codes (no ticket text)
    ingestion_report.json   counts, and the evaluation when --evaluate is given (no ticket text)

The ground-truth files (data/raw/expected_clean_tickets.csv, ingestion_expected_outcomes.jsonl) are only read by
--evaluate, never by the pipeline itself.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.ingest.pipeline import ACCEPT, CLEAN_FIELDS, HOLD, MERGE, NORMALIZE, QUARANTINE, Decision, Taxonomy, ingest  # noqa: E402

DATA = Path(__file__).resolve().parents[1] / "data"
ACTIONS = (ACCEPT, NORMALIZE, MERGE, QUARANTINE, HOLD)
COMPARED_FIELDS = ("created_at", "resolved_at", "status", "channel", "category", "subcategory", "priority",
                   "assigned_team", "affected_service", "environment", "subject", "description")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8", newline="\n")


def summary(decisions: list[Decision]) -> dict:
    return {
        "records": len(decisions),
        "actions": dict(Counter(d.action for d in decisions)),
        "reasons": dict(Counter(r for d in decisions for r in d.reasons)),
        "flags": dict(Counter(f for d in decisions for f in d.flags)),
        "must_redact": dict(Counter(k for d in decisions for k in d.must_redact)),
        "indexable": sum(d.indexable for d in decisions),
    }


def _pr(tp: int, predicted: int, actual: int) -> dict:
    return {"precision": round(tp / predicted, 4) if predicted else None,
            "recall": round(tp / actual, 4) if actual else None, "tp": tp, "predicted": predicted, "actual": actual}


def evaluate(decisions: list[Decision], raw_dir: Path) -> dict:
    """Score the decisions against the ground truth. Only IDs, codes and counts: no ticket text."""
    gt = {o["source_record_id"]: o for o in map(json.loads, (raw_dir / "ingestion_expected_outcomes.jsonl")
                                                  .read_text(encoding="utf-8").splitlines()) if o}
    expected = {r["source_record_id"]: r for r in read_csv(raw_dir / "expected_clean_tickets.csv")}
    mine = {d.source_record_id: d for d in decisions}
    assert set(gt) == set(mine), "the pipeline must decide every raw record exactly once"

    confusion = {a: dict(Counter(mine[i].action for i, o in gt.items() if o["ingestion_action"] == a)) for a in ACTIONS}
    per_action = {a: _pr(sum(1 for i in gt if gt[i]["ingestion_action"] == a and mine[i].action == a),
                         sum(1 for d in decisions if d.action == a),
                         sum(1 for o in gt.values() if o["ingestion_action"] == a)) for a in ACTIONS}

    # duplicates: a merge is right when it points at a record of the same curated ticket
    merges = [d for d in decisions if d.action == MERGE]
    right = [d for d in merges if gt[d.source_record_id]["curated_ticket_id"]
             and gt[d.canonical_id]["curated_ticket_id"] == gt[d.source_record_id]["curated_ticket_id"]]
    gt_merge = [i for i, o in gt.items() if o["ingestion_action"] == MERGE]
    duplicates = _pr(len(right), len(merges), len(gt_merge))
    duplicates["recall"] = round(sum(1 for d in right if gt[d.source_record_id]["ingestion_action"] == MERGE)
                                 / len(gt_merge), 4) if gt_merge else None

    # quarantine reasons: did we name the expected reason?
    q_ids = [i for i, o in gt.items() if o["ingestion_action"] == QUARANTINE]
    reason_hit = sum(1 for i in q_ids if mine[i].action == QUARANTINE
                     and set(gt[i]["quarantine_reasons"]) <= set(mine[i].reasons) | {"no_subject_or_description"}
                     or (mine[i].action == QUARANTINE and set(gt[i]["quarantine_reasons"]) & set(mine[i].reasons)))

    # clean values, where both sides keep the record
    field_total, field_ok, field_wrong = Counter(), Counter(), Counter()
    for i, d in mine.items():
        if d.action in (ACCEPT, NORMALIZE) and gt[i]["ingestion_action"] in (ACCEPT, NORMALIZE) and gt[i]["curated_ticket_id"]:
            for f in COMPARED_FIELDS:
                field_total[f] += 1
                if (d.clean.get(f) or "") == (expected[i].get(f) or ""):
                    field_ok[f] += 1
                else:
                    field_wrong[f] += 1
    fields = {f: round(field_ok[f] / field_total[f], 4) for f in COMPARED_FIELDS if field_total[f]}

    # clarify: ingestion flags records whose description is missing or near-empty (defects missing_description,
    # ambiguous_request). Vague-but-present text is the gate's call at analysis time, not ingestion's.
    kept = {i for i, o in gt.items() if o["ingestion_action"] in (ACCEPT, NORMALIZE)}
    clarify_gt = {i for i in kept if {"missing_description", "ambiguous_request"} & set(gt[i]["defects"])}
    clarify_me = {d.source_record_id for d in decisions if "needs_clarification" in d.flags} & kept
    # redaction: the ground truth lists only the personal data injected into raw text; the curated text already
    # holds synthetic emails and FAKE_TEST credentials on purpose, so precision is not measurable here: recall only
    redact_gt = {i: o["must_redact"] for i, o in gt.items() if o["must_redact"] and o["ingestion_action"] in (ACCEPT, NORMALIZE)}
    redact_missed = Counter(k for i, kinds in redact_gt.items() for k in kinds if not mine[i].must_redact)
    trusted_wrong = sum(1 for i, d in mine.items() if d.indexable and d.label_trusted
                        and expected[i].get("label_trusted") == "False")

    return {
        "action_accuracy": round(sum(1 for i in gt if mine[i].action == gt[i]["ingestion_action"]) / len(gt), 4),
        "confusion_expected_to_actual": confusion,
        "per_action": per_action,
        "duplicates": duplicates,
        "quarantine_reason_recall": round(reason_hit / len(q_ids), 4) if q_ids else None,
        "field_accuracy": fields,
        "field_mismatches": dict(field_wrong),
        "clarify": _pr(len(clarify_gt & clarify_me), len(clarify_me), len(clarify_gt)),
        "redaction_recall": round(sum(1 for i in redact_gt if mine[i].must_redact) / len(redact_gt), 4) if redact_gt else None,
        "redaction_missed_by_kind": dict(redact_missed),
        "security_missed_by_reason": dict(Counter(o.get("expected_reason") for i, o in gt.items()
                                                  if o["ingestion_action"] == HOLD and mine[i].action != HOLD)),
        "untrusted_labels_marked_trusted": trusted_wrong,
        # records the ground truth keeps but the pipeline holds for a human (never dropped), by reason
        "kept_records_held_for_review": dict(Counter(r for o in gt.values() if o["ingestion_action"] in (ACCEPT, NORMALIZE)
                                                     and mine[o["source_record_id"]].action in (QUARANTINE, HOLD)
                                                     for r in mine[o["source_record_id"]].reasons)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Raw-to-clean ITSM ingestion.")
    ap.add_argument("--raw", default=str(DATA / "raw" / "raw_tickets.csv"))
    ap.add_argument("--curated", default=str(DATA / "tickets" / "tickets.csv"), help="reference taxonomy")
    ap.add_argument("--out", default=str(DATA / "processed"))
    ap.add_argument("--now", help="ISO time treated as 'now' for future-timestamp checks (default: current time)")
    ap.add_argument("--evaluate", action="store_true", help="score against data/raw ground truth")
    args = ap.parse_args()

    raw_path, out = Path(args.raw), Path(args.out)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    decisions = ingest(read_csv(raw_path), Taxonomy.from_curated(read_csv(Path(args.curated))), now=now)

    out.mkdir(parents=True, exist_ok=True)
    kept = [d for d in decisions if d.indexable]
    write_csv(out / "clean_tickets.csv", [*CLEAN_FIELDS, "action", "label_trusted", "flags", "must_redact"],
              [{**d.clean, "action": d.action, "label_trusted": d.label_trusted, "flags": ";".join(d.flags),
                "must_redact": ";".join(d.must_redact)} for d in kept])
    write_csv(out / "duplicates.csv", ["source_record_id", "canonical_id"],
              [{"source_record_id": d.source_record_id, "canonical_id": d.canonical_id} for d in decisions if d.action == MERGE])
    for name, action in (("quarantine.csv", QUARANTINE), ("security_hold.csv", HOLD)):
        write_csv(out / name, ["source_record_id", "reasons"],
                  [{"source_record_id": d.source_record_id, "reasons": ";".join(d.reasons)} for d in decisions if d.action == action])
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "input": raw_path.name,
              **summary(decisions)}
    if args.evaluate:
        report["evaluation"] = evaluate(decisions, raw_path.parent)
    (out / "ingestion_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
