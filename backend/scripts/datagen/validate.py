"""Validate the curated and raw synthetic datasets. Read-only. Exit code 1 if any check fails.

Intentional raw defects are expected: they pass when the ground truth describes them exactly, and fail when a
defect appears that the ground truth does not record (or the other way round).

Usage (from backend): .\\.venv\\Scripts\\python.exe scripts\\datagen\\validate.py [--data backend/data]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from content import FAMILIES  # noqa: E402
from raw import (CATEGORY_ALIASES, CHANNEL_ALIASES, CLEAN_COLUMNS, ENV_ALIASES, MATRIX, PRIORITY_ALIASES,  # noqa: E402
                 RAW_COLUMNS, STATUS_ALIASES, SUBCATEGORY_ALIASES, TEAM_ALIASES, canon, clean_text, parse_ts)

CONTRACT = ["ticket_id", "created_at", "resolved_at", "status", "channel", "category", "subcategory", "priority",
            "assigned_team", "affected_service", "environment", "subject", "description", "root_cause",
            "resolution_notes", "resolution_steps", "problem_id", "dup_group_id", "split"]
GT_ONLY = {"ticket_id", "curated_ticket_id", "split", "dup_group_id", "problem_id", "ingestion_action", "label_trusted"}
fails: list[str] = []
info: list[str] = []


def check(ok: bool, msg: str) -> None:
    (info if ok else fails).append(("PASS  " if ok else "FAIL  ") + msg)


def read_csv(p: Path) -> tuple[list[str], list[dict]]:
    with p.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    bad = [i for i, r in enumerate(rows[1:], 2) if len(r) != len(rows[0])]
    check(not bad, f"{p.name}: every row has {len(rows[0])} fields" + (f" (bad lines {bad[:5]})" if bad else ""))
    return rows[0], [dict(zip(rows[0], r)) for r in rows[1:]]


def read_jsonl(p: Path) -> list[dict]:
    out, bad = [], 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
    check(bad == 0, f"{p.name}: every line is valid JSON ({len(out)} records)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[2] / "data"))
    data = Path(ap.parse_args().data)
    T, KB, R = data / "tickets", data / "docs" / "kb", data / "raw"

    # ================= CURATED =================
    hdr, tickets = read_csv(T / "tickets.csv")
    check(hdr == CONTRACT, "tickets.csv: header is the 19-column PLAN contract")
    tix = {t["ticket_id"]: t for t in tickets}
    check(len(tix) == len(tickets), f"tickets.csv: {len(tickets)} unique ticket_id")
    ts_bad = 0
    for t in tickets:
        c = datetime.fromisoformat(t["created_at"])
        if t["resolved_at"] and datetime.fromisoformat(t["resolved_at"]) < c:
            ts_bad += 1
        if (t["status"] in ("resolved", "closed")) != bool(t["resolved_at"]):
            ts_bad += 1
    check(ts_bad == 0, "tickets.csv: timestamps valid, resolved_at >= created_at, status agrees with resolved_at")
    for key in ("dup_group_id", "problem_id", "root_cause", "resolution_steps"):
        g = defaultdict(set)
        for t in tickets:
            if t[key]:
                g[t[key]].add(t["split"])
        check(all(len(v) == 1 for v in g.values()), f"leakage: no {key} value appears in two splits ({len(g)} values)")
    g = defaultdict(set)
    for t in tickets:
        g[t["subject"].lower().replace(" - multiple users", "")].add(t["split"])
    check(all(len(v) == 1 for v in g.values()), "leakage: no subject template appears in two splits")
    words = lambda s: set(re.findall(r"[a-z0-9]+", s.lower()))
    train_w = [words(t["subject"] + " " + t["description"]) for t in tickets if t["split"] == "train"]
    best = max(max(len(w & u) / len(w | u) for u in train_w)
               for w in (words(t["subject"] + " " + t["description"]) for t in tickets if t["split"] != "train"))
    check(best < 0.6, f"leakage: max word overlap of a val/test ticket with any train ticket = {best:.2f} (< 0.60)")

    kb = {p.stem: p.read_text(encoding="utf-8") for p in sorted(KB.glob("KB-*.md"))}
    check(sorted(kb) == [f"KB-{i:03d}" for i in range(1, 23)], "KB: KB-001..KB-022 present")
    for k, text in kb.items():
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        body = text[m.end():] if m else ""
        fm = m.group(1) if m else ""
        ok = all(f"\n{f}:" in "\n" + fm for f in ("version", "status", "updated", "doc_id")) and \
            "status: current" in fm and f"doc_id: {k}" in fm and body.lstrip().startswith("# ")
        n_words, n_h2 = len(body.split()), len(re.findall(r"^## ", body, re.M))
        check(ok and 400 <= n_words <= 800 and 4 <= n_h2 <= 6, f"{k}: front matter, title, {n_words} words, {n_h2} sections")

    labels = read_jsonl(T / "labelled_cases.jsonl")
    errs = 0
    for x in labels:
        if x.get("ticket_id"):
            t = tix.get(x["ticket_id"])
            errs += t is None or t["split"] != x["split"] or x["split"] == "train"
            errs += any(s not in tix or tix[s]["split"] != x["split"] for s in x["gold_similar_ticket_ids"])
            errs += x["gold_kb_doc_id"] not in kb or kb[x["gold_kb_doc_id"]].count(x["key_sentence"]) != 1
        else:
            errs += x.get("source") != "authored" or not (x.get("ticket") or {}).get("description")
            errs += bool(x["gold_similar_ticket_ids"]) or any(d not in kb for d in x["gold_kb_doc_ids"])
    check(errs == 0, "labelled_cases: ticket/KB references exist, same split, key_sentence unique in its KB, no train cases")
    check({x["expected_outcome"] for x in labels} == {"RECOMMEND", "CLARIFY", "ESCALATE", "BLOCKED"},
          f"labelled_cases: all 4 outcomes covered {dict(Counter(x['expected_outcome'] for x in labels))}")
    _, pairs = read_csv(T / "retrieval_pairs.csv")
    perr = sum(p["query_ticket_id"] not in tix or p["candidate_ticket_id"] not in tix or
               tix[p["candidate_ticket_id"]]["split"] != p["split"] or tix[p["query_ticket_id"]]["split"] != p["split"]
               for p in pairs)
    check(perr == 0, f"retrieval_pairs: {len(pairs)} pairs, all ids exist and stay within their split")
    rt = read_jsonl(T / "redteam.jsonl")
    _, fb = read_csv(T / "feedback_examples.csv")
    check(all(r["verdict"] in ("useful", "incorrect", "unresolved") for r in fb), f"feedback_examples: {len(fb)} valid verdicts")

    # ================= RAW + GROUND TRUTH =================
    rhdr, raw = read_csv(R / "raw_tickets.csv")
    check(rhdr == RAW_COLUMNS, "raw_tickets.csv: header matches the raw schema")
    check(not (set(rhdr) & GT_ONLY), "raw_tickets.csv: no ground-truth columns (ticket_id, split, dup_group_id, ...)")
    chdr, clean = read_csv(R / "expected_clean_tickets.csv")
    check(chdr == CLEAN_COLUMNS, "expected_clean_tickets.csv: header")
    _, manifest = read_csv(R / "defect_manifest.csv")
    outcomes = read_jsonl(R / "ingestion_expected_outcomes.jsonl")
    ids = [r["source_record_id"] for r in raw]
    check(len(set(ids)) == len(ids) and all(re.fullmatch(r"RAW-\d{6}", i) for i in ids),
          f"raw: {len(ids)} unique stable source_record_id values")
    check([c["source_record_id"] for c in clean] == ids and [o["source_record_id"] for o in outcomes] == ids,
          "ground truth: expected_clean and outcomes have exactly one row per raw record, same order")
    check(all(d["source_record_id"] in set(ids) for d in manifest), f"defect_manifest: {len(manifest)} defects, all point to raw records")
    O = {o["source_record_id"]: o for o in outcomes}
    C = {c["source_record_id"]: c for c in clean}
    man = defaultdict(set)
    for d in manifest:
        man[d["source_record_id"]].add(d["defect_type"])
    check(all(set(O[i]["defects"]) == man[i] for i in ids), "ground truth: outcome defect lists match the manifest")

    # mapping and duplicates
    mapped = [o for o in outcomes if o["curated_ticket_id"]]
    base = Counter(o["curated_ticket_id"] for o in mapped if "cross_channel_duplicate" not in o["defects"])
    check(set(base) == set(tix) and all(v == 1 for v in base.values()),
          f"mapping: every curated ticket has exactly one original raw record ({len(base)})")
    check(all(o["curated_ticket_id"] in tix for o in mapped), "mapping: every curated_ticket_id exists in tickets.csv")
    check(all(o["ingestion_action"] in ("MERGE_DUPLICATE", "QUARANTINE") for o in mapped
              if "cross_channel_duplicate" in o["defects"]), "duplicates: extra submissions are merged (or quarantined on ref collision)")

    # leakage
    check(all(o["split"] == tix[o["curated_ticket_id"]]["split"] and C[o["source_record_id"]]["split"] == o["split"]
              for o in mapped), "leakage: every raw record keeps its curated ticket's split")
    check(all(C[o["source_record_id"]]["dup_group_id"] == tix[o["curated_ticket_id"]]["dup_group_id"] for o in mapped),
          "leakage: duplicate submissions stay in the same duplicate group as their ticket")
    check(not any(o["use_for_training"] and o["split"] != "train" for o in outcomes), "leakage: no val/test/extra record is marked for training")
    eval_ids = {x["ticket_id"] for x in labels if x.get("ticket_id")}
    check(not any(o["use_for_training"] and o["curated_ticket_id"] in eval_ids for o in outcomes), "leakage: no evaluation ticket is marked for training")
    check(not any(o["use_for_training"] and C[o["source_record_id"]]["label_trusted"] != "True" for o in outcomes),
          "labels: records with corrupted or missing labels are never marked for training")
    check(all(o["split"] in ("val", "test", "none") for o in outcomes if not o["curated_ticket_id"]),
          "leakage: extra raw records (junk, ambiguous, security) are never in train")

    # reference normaliser: recoverable defects must be fixable with the documented tables alone
    by_service = defaultdict(list)
    for f in FAMILIES:
        by_service[f.service].append(f)
    norm_err = Counter()
    for r in raw:
        o, c = O[r["source_record_id"]], C[r["source_record_id"]]
        if o["ingestion_action"] not in ("ACCEPT", "NORMALIZE", "MERGE_DUPLICATE") or not o["curated_ticket_id"]:
            continue
        sysname = r["source_system"]
        pr = canon(r["priority"], PRIORITY_ALIASES)
        if pr is None:
            lv = (int(r["impact"][0]), int(r["urgency"][0]))
            pr = next(p for p, cells in MATRIX.items() if lv in cells)
        norm_err["priority"] += pr != c["priority"]
        norm_err["status"] += canon(r["status"], STATUS_ALIASES) != c["status"]
        norm_err["channel"] += canon(r["channel"], CHANNEL_ALIASES) != c["channel"]
        norm_err["environment"] += canon(r["environment"], ENV_ALIASES) != c["environment"]
        fams = by_service.get(r["affected_service"], [])
        if len(fams) > 1:
            sub = canon(r["subcategory"], {f.subcategory: SUBCATEGORY_ALIASES.get(f.subcategory, []) for f in fams})
            fams = [f for f in fams if f.subcategory == sub]
        if len(fams) == 1:
            f = fams[0]
            norm_err["category"] += (f.category, f.subcategory, f.team) != (c["category"], c["subcategory"], c["assigned_team"])
        elif "wrong_category" not in o["defects"]:
            norm_err["category_unresolvable"] += 1
        cr = parse_ts(r["created_at"], sysname)
        norm_err["created_at"] += cr is None or cr.isoformat(timespec="seconds") != c["created_at"]
        rs = parse_ts(r["resolved_at"], sysname)
        norm_err["resolved_at"] += (rs.isoformat(timespec="seconds") if rs else "") != c["resolved_at"]
        norm_err["subject"] += clean_text(r["subject"]) != c["subject"]
        norm_err["description"] += clean_text(r["description"]) != c["description"]
    for k, v in sorted(norm_err.items()):
        check(v == 0, f"normalisation: reference cleaner reproduces expected {k} for every accepted record (mismatches {v})")

    # quarantine decisions: each reason is really present; accepted records have no hidden contradictions
    ref_count = Counter(r["source_ticket_ref"] for r in raw)
    def contradictions(r: dict) -> set[str]:
        s, found = r["source_system"], set()
        cr, rs = parse_ts(r["created_at"], s), parse_ts(r["resolved_at"], s)
        if cr is None:
            found.add("invalid_timestamp")
        if cr and rs and rs < cr:
            found.add("resolved_before_created")
        st = canon(r["status"], STATUS_ALIASES)
        if st in ("resolved", "closed") and not r["resolved_at"]:
            found.add("closed_without_resolved_at")
        if st in ("open", "in_progress") and r["resolved_at"]:
            found.add("open_with_resolved_at")
        cat = canon(r["category"], CATEGORY_ALIASES)
        team = canon(r["assigned_team"], TEAM_ALIASES)
        fams = by_service.get(r["affected_service"], [])
        if cat and team and fams and cat not in {f.category for f in fams} and team not in {f.team for f in fams}:
            found.add("category_service_conflict")       # documented rule: category and team both disagree
        pr = canon(r["priority"], PRIORITY_ALIASES)
        if pr and r["impact"] and (int(r["impact"][0]), int(r["urgency"][0])) not in MATRIX[pr]:
            found.add("priority_matrix_conflict")
        if ref_count[r["source_ticket_ref"]] > 1:
            found.add("source_ref_collision")
        return found
    q_err, hidden = 0, 0
    for r in raw:
        o = O[r["source_record_id"]]
        if not o["curated_ticket_id"]:
            continue
        found = contradictions(r)
        if o["ingestion_action"] == "QUARANTINE":
            reasons = set(o["quarantine_reasons"])
            q_err += not reasons <= found | {"source_ref_collision"}
        else:
            # the first record holding a collided ref is legitimate; only the later copy is quarantined
            hidden += bool(found - {"source_ref_collision"})
    check(q_err == 0, "quarantine: every quarantine reason is really present in the raw record")
    check(hidden == 0, f"quarantine: no accepted record hides an unflagged contradiction (found {hidden})")
    sec = [o for o in outcomes if o["ingestion_action"] == "HOLD_SECURITY"]
    check(all(o["expected_outcome"] in ("ESCALATE", "BLOCKED") and not o["use_for_index"] for o in sec),
          f"security: {len(sec)} held records expect ESCALATE/BLOCKED and are never indexed")
    amb = [o for o in outcomes if "ambiguous_request" in o["defects"] or "missing_description" in o["defects"]]
    check(all(o["expected_outcome"] in ("CLARIFY", None) for o in amb), f"clarify: {len(amb)} incomplete records expect CLARIFY")
    check(all(not o["use_for_index"] for o in outcomes if o["ingestion_action"] in ("QUARANTINE", "HOLD_SECURITY", "MERGE_DUPLICATE")),
          "index: quarantined, held and merged records are never indexed")
    expected_clean_fields = [k for k in CONTRACT if k not in ("subject", "description", "channel", "created_at")]
    gt_err = sum(any(C[o["source_record_id"]][k] != tix[o["curated_ticket_id"]][k] for k in expected_clean_fields)
                 for o in mapped)
    check(gt_err == 0, "ground truth: expected clean labels and resolution fields equal the curated ticket")

    # secrets: only synthetic placeholders allowed anywhere in the data
    real = {"AIza": r"AIza[0-9A-Za-z_\-]{30,}", "gsk_": r"gsk_[0-9A-Za-z]{20,}", "sk-": r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}",
            "AKIA": r"AKIA[0-9A-Z]{16}", "gh_": r"gh[pousr]_[A-Za-z0-9]{30,}", "xox": r"xox[abpr]-[0-9A-Za-z\-]{10,}",
            "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "jwt": r"eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"}
    hits = Counter()
    files = [p for p in data.rglob("*") if p.is_file() and p.suffix in (".csv", ".jsonl", ".md")]
    for p in files:
        s = p.read_text(encoding="utf-8")
        for name, rx in real.items():
            hits[name] += len(re.findall(rx, s))
        for m in re.finditer(r"(?i)(password|pwd|passwd|key|token)\s*(?:is|=|:)\s*\(?([A-Za-z0-9_\-]{6,})", s):
            hits["non-placeholder credential"] += not m.group(2).upper().startswith("FAKE_TEST")
        for m in re.finditer(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", s):
            hits["non-example email domain"] += m.group(1).lower() not in ("example.invalid",)
    check(sum(hits.values()) == 0, f"secrets: no real-format keys, credentials or real email domains in {len(files)} files {dict(+hits)}")

    # ================= REPORT =================
    print("=== COUNTS")
    print(f"curated tickets {len(tickets)} {dict(Counter(t['split'] for t in tickets))}; labelled {len(labels)}; "
          f"pairs {len(pairs)}; KB {len(kb)}; redteam {len(rt)}; feedback {len(fb)}")
    print(f"raw records {len(raw)}: actions {dict(Counter(o['ingestion_action'] for o in outcomes))}")
    print(f"raw splits {dict(Counter(o['split'] for o in outcomes))}; mapped {len(mapped)}, extra {len(raw) - len(mapped)}")
    print(f"quarantine reasons {dict(Counter(q for o in outcomes for q in o['quarantine_reasons']))}")
    print(f"defects {len(manifest)} by type: {dict(Counter(d['defect_type'] for d in manifest).most_common())}")
    print(f"records with at least one defect: {sum(1 for o in outcomes if o['defects'])}")
    print(f"expected outcomes on raw: {dict(Counter(o['expected_outcome'] for o in outcomes if o['expected_outcome']))}")
    print(f"=== CHECKS: {len(info)} passed, {len(fails)} failed")
    for line in fails + info:
        print(" ", line)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
