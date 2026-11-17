"""Build the synthetic ITSM dataset from content.py and authored_cases.py (deterministic, seed 20261117).

Writes (relative to --out, default backend/data). Never overwrites a file whose content differs unless --force;
identical files are skipped, so rerunning with the same seed and content changes nothing.
  tickets/tickets.csv            1100 tickets, the 19 columns of the docs/PLAN.md data contract
  tickets/labelled_cases.jsonl   gold cases for every val/test ticket + authored ESCALATE / BLOCKED cases
  tickets/retrieval_pairs.csv    labelled query/candidate pairs within each split
  docs/kb/KB-001.md .. KB-022.md one article per family

  raw/raw_tickets.csv, raw/defect_manifest.csv, raw/expected_clean_tickets.csv,
  raw/ingestion_expected_outcomes.jsonl   dirty RAW layer + its ground truth (see raw.py)

Does not touch tickets/redteam.jsonl or tickets/feedback_examples.csv.

Split rule: each family has 5 scenarios (distinct root cause, wording and fix). Whole scenarios go to one split:
3 train, 1 val, 1 test per family. Duplicate groups and problem records live inside one scenario, so nothing
that shares a template, a duplicate group or a problem record crosses splits.

Usage (from backend): .\\.venv\\Scripts\\python.exe scripts\\datagen\\build.py [--force]
Then validate:        .\\.venv\\Scripts\\python.exe scripts\\datagen\\validate.py
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from authored_cases import CASES
from content import FAMILIES
from raw import CLEAN_COLUMNS, MANIFEST_COLUMNS, RAW_COLUMNS, make_raw

SEED = 20261117
COLUMNS = ["ticket_id", "created_at", "resolved_at", "status", "channel", "category", "subcategory", "priority",
           "assigned_team", "affected_service", "environment", "subject", "description", "root_cause",
           "resolution_notes", "resolution_steps", "problem_id", "dup_group_id", "split"]
TRAIN_SIZES, VAL_SIZE, TEST_SIZE = [12, 12, 11], 8, 7          # tickets per scenario -> 50 per family, 1100 total
START, RECENT, END = datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 10, 27, tzinfo=timezone.utc), \
    datetime(2026, 11, 14, tzinfo=timezone.utc)
SITES = ["London", "Pune", "Chennai", "Austin", "Krakow", "Manila", "Leeds", "Toronto"]
PRIO = ["P1", "P2", "P3", "P4"]
HOURS = {"P1": (1, 8), "P2": (4, 36), "P3": (12, 96), "P4": (24, 200)}
CLOSINGS = ["Fix confirmed with the requester.", "Service recovered and monitored for one hour.",
            "Documented steps applied; no recurrence observed.", "Requester confirmed the issue is resolved.",
            "Resolved and verified by the resolver group."]
CONTEXT = ["First seen around {hh}:{mm}.", "Affects {n} users at the {site} office.",
           "Reported by the {site} team lead.", "First noticed after the weekend.",
           "No workaround found so far.", "User has already restarted the device.",
           "Impact: users cannot finish their daily work.", "Seen in the {site} office only."]


def shift(prio: str, steps: int) -> str:
    return PRIO[max(0, min(3, PRIO.index(prio) + steps))]


def iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="seconds") if dt else ""


def group_sizes(rng: random.Random, n: int) -> list[int]:
    sizes = []
    while n > 0:
        s = min(n, rng.choice([2, 2, 3, 3, 4]))
        if n - s == 1:                       # never leave a singleton group
            s += 1
        sizes.append(s)
        n -= s
    return sizes


def make_tickets(rng: random.Random) -> list[dict]:
    rows, mask_quota = [], {"email": 27, "password": 12, "host": 30}
    for fi, fam in enumerate(FAMILIES):
        val_i, test_i = fi % 5, (fi + 2) % 5
        train_sizes = iter(TRAIN_SIZES)
        dup_n = 0
        for si, sc in enumerate(fam.scenarios):
            split = "val" if si == val_i else "test" if si == test_i else "train"
            n = VAL_SIZE if split == "val" else TEST_SIZE if split == "test" else next(train_sizes)
            problem = f"PRB-{int(fam.fid) * 10 + si:04d}"
            for gi, size in enumerate(group_sizes(rng, n)):
                dup_n += 1
                dup = f"DUP-{fam.fid}-{dup_n:03d}"
                recent = gi > 0 and rng.random() < 0.15     # first group is historical: every scenario has resolved tickets
                lo, hi = (RECENT, END) if recent else (START, RECENT)
                base = lo + timedelta(minutes=rng.randrange(int((hi - lo).total_seconds() // 60)))
                env = rng.choices(["production", "staging", "development"], [75, 15, 10])[0]
                for k in range(size):
                    created = base + timedelta(minutes=rng.randrange(0, 360) if k else 0)
                    open_p = 0.7 if recent else 0.03
                    status = rng.choice(["open", "in_progress"]) if rng.random() < open_p else \
                        ("closed" if rng.random() < 0.2 else "resolved")
                    prio = sc.prio
                    if env == "development" and rng.random() < 0.5:
                        prio = shift(prio, 1)
                    elif env == "staging" and rng.random() < 0.25:
                        prio = shift(prio, 1)
                    if rng.random() < 0.08:                      # deliberate label noise
                        prio = shift(prio, rng.choice([-1, 1]))
                    sparse = rng.random() < (0.15 if split != "train" else 0.04)
                    if sparse:
                        subject, desc = sc.sparse.rstrip("."), sc.sparse
                    else:
                        subject = rng.choice(sc.subjects)
                        if size >= 3 and k == size - 1 and rng.random() < 0.5:
                            subject += " - multiple users"
                        parts = [rng.choice(sc.descriptions)]
                        if rng.random() < 0.55:
                            parts.append(f"Error shown: {sc.signal}.")
                        if rng.random() < 0.6:
                            parts.append(rng.choice(CONTEXT).format(hh=f"{rng.randrange(7, 19):02d}",
                                         mm=rng.choice(["00", "15", "30", "45"]), n=rng.randrange(2, 40),
                                         site=rng.choice(SITES)))
                        if mask_quota["host"] and rng.random() < 0.05:
                            mask_quota["host"] -= 1
                            parts.append(f"Host: srv-{rng.randrange(10, 99)}.internal.example.")
                        if mask_quota["email"] and rng.random() < 0.04:
                            mask_quota["email"] -= 1
                            parts.append(f"Contact: analyst{rng.randrange(1, 60)}@example.invalid.")
                        if mask_quota["password"] and rng.random() < 0.02:
                            mask_quota["password"] -= 1
                            parts.append(f"User pasted a test credential: password=FAKE_TEST_{rng.randrange(1000, 9999)}.")
                        desc = " ".join(parts)
                    done = status in ("resolved", "closed")
                    resolved = created + timedelta(hours=rng.uniform(*HOURS[prio])) if done else None
                    if fam.monitored:
                        channel = rng.choices(["monitoring", "portal", "email", "chat", "phone"], [35, 20, 15, 15, 15])[0]
                    elif fam.fid == "018":
                        channel = rng.choices(["email", "portal", "phone"], [60, 25, 15])[0]
                    else:
                        channel = rng.choice(["portal", "email", "chat", "phone"])
                    rows.append({
                        "created_at": created, "resolved_at": resolved, "status": status, "channel": channel,
                        "category": fam.category, "subcategory": fam.subcategory, "priority": prio,
                        "assigned_team": fam.team, "affected_service": fam.service, "environment": env,
                        "subject": subject, "description": desc,
                        "root_cause": sc.cause if done else "",
                        "resolution_notes": f"Root cause: {sc.cause.lower()}. {rng.choice(CLOSINGS)}" if done else "",
                        "resolution_steps": " | ".join(sc.steps) if done else "",
                        "problem_id": problem if size >= 3 else "", "dup_group_id": dup, "split": split,
                        "_fam": fam, "_sc": sc, "_sparse": sparse,
                    })
    rows.sort(key=lambda r: (r["created_at"], r["dup_group_id"]))
    for i, r in enumerate(rows, 1):
        r["ticket_id"] = f"INC-{100000 + i}"
    return rows


def make_labels(rows: list[dict]) -> list[dict]:
    by_group: dict[str, list[str]] = {}
    for r in rows:
        by_group.setdefault(r["dup_group_id"], []).append(r["ticket_id"])
    out, n = [], 0
    for r in rows:
        if r["split"] == "train":
            continue
        n += 1
        sc, fam = r["_sc"], r["_fam"]
        clarify = r["_sparse"]
        out.append({
            "case_id": f"EVAL-{n:05d}", "ticket_id": r["ticket_id"], "split": r["split"],
            "expected_category": r["category"], "expected_subcategory": r["subcategory"],
            "expected_priority": r["priority"],
            "expected_outcome": "CLARIFY" if clarify else "RECOMMEND",
            "expected_reason": "insufficient_detail" if clarify else "evidence_available",
            "gold_similar_ticket_ids": [t for t in by_group[r["dup_group_id"]] if t != r["ticket_id"]],
            "gold_kb_doc_ids": [fam.kb], "gold_kb_doc_id": fam.kb, "key_sentence": sc.steps[sc.fix] + ".",
            "issue_group": r["dup_group_id"],
            "reason": "Description too vague to act on: no error, scope or timing" if clarify
                      else "Same-issue tickets and a matching KB article exist",
            "source": "tickets.csv", "ticket": None,
        })
    counts = {"ESCALATE": 0, "BLOCKED": 0}
    for (key, split, outcome, reason, cat, sub, prio, kbs, subject, desc, svc, env, channel, why) in CASES:
        counts[outcome] += 1
        out.append({
            "case_id": f"EVAL-{'ESC' if outcome == 'ESCALATE' else 'BLK'}-{counts[outcome]:03d}",
            "ticket_id": None, "split": split, "expected_category": cat, "expected_subcategory": sub,
            "expected_priority": prio, "expected_outcome": outcome, "expected_reason": reason,
            "gold_similar_ticket_ids": [], "gold_kb_doc_ids": kbs, "gold_kb_doc_id": kbs[0] if kbs else None,
            "key_sentence": None, "issue_group": f"AUTHORED-{key}", "reason": why, "source": "authored",
            "ticket": {"subject": subject, "description": desc, "affected_service": svc,
                       "environment": env, "channel": channel},
        })
    return out


def make_pairs(rows: list[dict], rng: random.Random) -> list[dict]:
    pairs = []
    for split in ("val", "test"):
        pool = [r for r in rows if r["split"] == split]
        for q in pool:
            same_group = [r for r in pool if r["dup_group_id"] == q["dup_group_id"] and r is not q]
            same_sc = [r for r in pool if r["_sc"] is q["_sc"] and r["dup_group_id"] != q["dup_group_id"]]
            look = [r for r in pool if r["_fam"] is not q["_fam"] and
                    (r["category"] == q["category"] or r["affected_service"] == q["affected_service"])]
            other = [r for r in pool if r["_fam"] is not q["_fam"] and r not in look]
            picks = [(r, 2) for r in rng.sample(same_group, min(3, len(same_group)))]
            picks += [(r, 1) for r in rng.sample(same_sc, min(1, len(same_sc)))]
            picks += [(r, 0) for r in rng.sample(look, min(1, len(look)))]
            picks += [(r, 0) for r in rng.sample(other, 6 - len(picks))]
            for c, rel in picks:
                pairs.append({"query_ticket_id": q["ticket_id"], "candidate_ticket_id": c["ticket_id"],
                              "relevance": rel, "split": split,
                              "same_service": c["affected_service"] == q["affected_service"],
                              "lookalike": rel == 0 and c in look})
    return pairs


def make_kb(fam) -> str:
    lines = ["---", 'version: "2.0"', "status: current", "updated: 2026-11-17", f"doc_id: {fam.kb}",
             f'category: "{fam.category}"', f'subcategory: "{fam.subcategory}"', f'service: "{fam.service}"',
             f'owner: "{fam.team}"', "synthetic: true", "---", "", f"# {fam.title}", "",
             "## Symptoms and scope", "", fam.intro, "", "This guide covers these known causes:", ""]
    lines += [f"- {s.cause} (typical signal: `{s.signal}`)" for s in fam.scenarios]
    lines += ["", "Synthetic guidance written for development and evaluation; it is not the approved procedure "
              "of any real organisation.", "",
              "## Before you start", "", fam.prereq, "",
              "Record the ticket ID, the affected users or systems, the start time and any recent changes before "
              "you act. Do not assume a cause from symptoms alone: match the observed signal to a cause below.", "",
              "## Diagnostic checks", ""]
    lines += [f"- **{s.cause}.** {s.diag}" for s in fam.scenarios]
    lines += ["", "## Resolution steps by cause", ""]
    for s in fam.scenarios:
        lines += [f"### {s.cause}", ""] + [f"{i}. {step}." for i, step in enumerate(s.steps, 1)] + [""]
    lines += ["## Validation and escalation", "", fam.validate, "", fam.escalate, "",
              "Also escalate when the observed facts match none of the causes above, when a step fails, or when "
              "the impact grows while you work. Record each step taken and its result in the ticket.", ""]
    return "\n".join(lines)


def csv_text(cols: list[str], rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def jsonl_text(rows: list[dict]) -> str:
    return "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the synthetic curated + raw ITSM datasets (deterministic).")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[2] / "data"),
                    help="data folder to write into (default backend/data)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite files whose content differs (without it, differing files are left untouched)")
    args = ap.parse_args()
    out = Path(args.out)
    rng = random.Random(SEED)
    rows = make_tickets(rng)
    labels = make_labels(rows)
    pairs = make_pairs(rows, rng)
    raw = make_raw(rows, labels, FAMILIES, iso)

    files: dict[Path, str] = {
        out / "tickets" / "tickets.csv": csv_text(
            COLUMNS, [{**r, "created_at": iso(r["created_at"]), "resolved_at": iso(r["resolved_at"])} for r in rows]),
        out / "tickets" / "labelled_cases.jsonl": jsonl_text(labels),
        out / "tickets" / "retrieval_pairs.csv": csv_text(
            ["query_ticket_id", "candidate_ticket_id", "relevance", "split", "same_service", "lookalike"], pairs),
        out / "raw" / "raw_tickets.csv": csv_text(RAW_COLUMNS, raw["raw"]),
        out / "raw" / "defect_manifest.csv": csv_text(MANIFEST_COLUMNS, raw["manifest"]),
        out / "raw" / "expected_clean_tickets.csv": csv_text(CLEAN_COLUMNS, raw["clean"]),
        out / "raw" / "ingestion_expected_outcomes.jsonl": jsonl_text(raw["outcomes"]),
    }
    for fam in FAMILIES:
        files[out / "docs" / "kb" / f"{fam.kb}.md"] = make_kb(fam)

    written, same, refused = [], [], []
    for path, text in files.items():
        if path.exists() and path.read_text(encoding="utf-8") == text:
            same.append(path)
            continue
        if path.exists() and not args.force:
            refused.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    print(f"tickets {len(rows)}, labelled {len(labels)}, pairs {len(pairs)}, kb {len(FAMILIES)}, "
          f"raw {len(raw['raw'])}, defects {len(raw['manifest'])} -> {out}")
    print(f"written {len(written)}, unchanged {len(same)}, left untouched (differs; use --force) {len(refused)}")
    for p in refused:
        print("  differs:", p)
    if refused:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
