"""After-every-merge check: run analyze() on every demo preset and print what came out.

Run from the backend folder:
    .\\.venv\\Scripts\\python.exe scripts\\smoke_pipeline.py

Prints one row per preset (app/tickets/presets.json): id, expected, got, reasons, generator, ms.
Exit code 1 only when a case that does NOT depend on data differs from what we expect: BLOCKED,
SAFETY and TOO_VAGUE are decided by fixed words and rules. The other outcomes depend on the ticket data,
the classifier and the KB, so a difference there is shown ("data") but does not fail the run.
Presets copied from tickets.csv are searched with their own source_ticket_id excluded (leave-one-out).
Prints no ticket text beyond the preset labels.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.common.env import load_dotenv  # noqa: E402
from app.tickets.models import TicketIn, User  # noqa: E402
from app.tickets.pipeline import analyze  # noqa: E402

PRESETS = BACKEND_DIR / "app" / "tickets" / "presets.json"
DATA_FREE_REASONS = {"SAFETY", "TOO_VAGUE"}


def is_strict(preset: dict) -> bool:
    return preset["expected_outcome"] == "BLOCKED" or preset.get("expected_reason") in DATA_FREE_REASONS


def matches(preset: dict, outcome: str, reasons: list[str]) -> bool:
    """Right outcome for the right reason (the expected reason code must be among the reasons)."""
    reason = preset.get("expected_reason")
    # SAFETY also matches the screen's specific hazard codes (SAFETY_SMOKE, ...), which the gate now reports.
    found = reason is None or any(r == reason or (reason == "SAFETY" and r.startswith("SAFETY_")) for r in reasons)
    return outcome == preset["expected_outcome"] and found


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.ERROR)
    presets = json.loads(PRESETS.read_text(encoding="utf-8"))
    user = User(username="smoke", role="analyst")
    header = f"{'id':22} {'expected':10} {'got':10} {'check':6} {'reasons':36} {'generator':9} {'ms':>7}"
    print(header)
    print("-" * len(header))
    failures = 0
    for preset in presets:
        # Leave-one-out: a preset copied from tickets.csv is also in the index; it must not find itself.
        own_id = preset.get("source_ticket_id")
        result = analyze(TicketIn(**preset["ticket"]), user, exclude_ticket_ids=[own_id] if own_id else [])
        ok = matches(preset, result.outcome, result.reasons)
        strict = is_strict(preset)
        check = "ok" if ok else ("FAIL" if strict else "data")
        failures += (not ok) and strict
        generator = result.recommendation.generator if result.recommendation else "-"
        ms = sum(result.timings_ms.values())
        print(f"{preset['id']:22} {preset['expected_outcome']:10} {result.outcome:10} {check:6} "
              f"{', '.join(result.reasons)[:36]:36} {generator:9} {ms:7.1f}")
    print(f"\n{len(presets)} presets, {failures} data-free failure(s). 'data' = depends on tickets/classifier/KB.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
