"""What the ticket classifier reads (FR-01). One recipe, used by training AND by classify() at run time.

If training built the text one way and the live classifier another way, the model would see a different kind
of input in production than it learned from, and its accuracy would quietly drop. So both call ticket_text().
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

# Change this string whenever ticket_text() changes: saved embeddings record it, so training can tell that
# features built with an older recipe are stale.
TEXT_RECIPE = "v1: subject + '. ' + description, whitespace collapsed"

TEXT_COLUMNS: tuple[str, ...] = ("subject", "description")   # the only columns the text is built from

LEAKY_COLUMNS: frozenset[str] = frozenset({
    "root_cause", "resolution_notes", "resolution_steps", "resolved_at", "status",
    "category", "subcategory", "priority", "assigned_team", "problem_id", "dup_group_id",
})
"""Columns that must never be used as features.

They are only known AFTER a ticket has been worked on: the root cause, the fix, when and by whom it was resolved,
its final status, its problem record and duplicate group. category / subcategory / priority are the labels the
classifier predicts. A new ticket arriving at the assistant has none of these, so a model that learned from them
would look excellent in evaluation and fail on real tickets (target leakage).
"""

_SPACE = re.compile(r"\s+")


def ticket_text(subject: Optional[str], description: Optional[str]) -> str:
    """subject + ". " + description, with every run of whitespace (spaces, tabs, newlines) collapsed to one space.
    A missing part is left out rather than leaving a stray ". "."""
    s = _SPACE.sub(" ", subject or "").strip()
    d = _SPACE.sub(" ", description or "").strip()
    return f"{s}. {d}" if s and d else s or d


def row_text(row: Mapping[str, Any]) -> str:
    """ticket_text() for a tickets.csv row (or any mapping with the data's own field names). Reads only
    TEXT_COLUMNS, so leaky columns in the row can never reach the text."""
    return ticket_text(row.get("subject"), row.get("description"))


assert not LEAKY_COLUMNS & set(TEXT_COLUMNS), "a text column is listed as leaky"
