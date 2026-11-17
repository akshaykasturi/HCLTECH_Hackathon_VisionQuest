"""Step 2: mask the ticket. After this step nothing raw goes anywhere: not to search, storage, logs,
the trace or the LLM.

    masked, counts = mask_ticket(ticket)     # counts: {"email": 1, "phone": 1, "password": 1}

Every text field goes through common/redact.mask_text (tokens, API keys, emails, IPs, phone numbers,
passwords written in the text). Non-text fields (created_at) are kept as they are.

The counts are safe to show and store ("2 emails were hidden"): they never contain the values.

Known limit: people's names are not masked (no reliable pattern without a name model). The ticket data is
synthetic, and names in a ticket are usually the requester's own; we say this in the README.
"""
from __future__ import annotations

from collections import Counter

from app.common.redact import mask_text
from app.tickets.models import TicketIn

TEXT_FIELDS = ("subject", "description", "affected_service", "environment", "channel")


def mask_ticket(ticket: TicketIn) -> tuple[TicketIn, dict[str, int]]:
    """A masked copy of the ticket (the input is not changed) and how many of each secret kind were hidden."""
    counts: Counter[str] = Counter()
    updates: dict[str, str] = {}
    for field in TEXT_FIELDS:
        value = getattr(ticket, field)
        if value:
            masked, found = mask_text(value)
            counts.update(found)
            if masked != value:
                updates[field] = masked
    # model_copy skips validation on purpose: "<PASSWORD>" may be longer than the value it replaced,
    # and a masked ticket must never be rejected for that.
    return ticket.model_copy(update=updates), dict(counts)
