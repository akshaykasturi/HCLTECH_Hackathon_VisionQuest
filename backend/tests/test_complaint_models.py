"""Employee complaint shapes (app/tickets/models.py): validation, and the TicketIn additions."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.auth.deps import ROLE_LEVEL
from app.tickets.models import (ComplaintIn, ComplaintReceipt, ComplaintSummary, TicketIn, TimelineEvent, User)

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
COMPLAINT = {"subject": "Laptop will not start", "description": "Black screen since this morning.",
             "impact": "cannot_work", "category_hint": "Endpoint / Laptop"}


def test_employee_is_a_role_below_analyst():
    assert User(username="emma", role="employee").role == "employee"
    assert ROLE_LEVEL["employee"] < ROLE_LEVEL["analyst"] < ROLE_LEVEL["lead"]
    with pytest.raises(ValidationError):
        User(username="x", role="admin")


def test_ticket_impact_and_category_hint_are_optional_and_validated():
    t = TicketIn(subject="VPN error 809", description="Fails from home.")
    assert t.impact is None and t.category_hint is None
    assert TicketIn(subject="VPN error 809", description="x", impact="many_people").impact == "many_people"
    with pytest.raises(ValidationError):
        TicketIn(subject="VPN error 809", description="x", impact="urgent!!!")       # not one of the 4 levels
    with pytest.raises(ValidationError):
        TicketIn(subject="VPN error 809", description="x", category_hint="N" * 51)   # max 50
    for smuggled in ("mail me at anna@example.com", "key: sk-abc", "<script>"):
        with pytest.raises(ValidationError):
            TicketIn(subject="VPN error 809", description="x", category_hint=smuggled)


def test_complaint_becomes_a_portal_ticket():
    c = ComplaintIn(**COMPLAINT)
    t = c.to_ticket(created_at=NOW)
    assert (t.subject, t.channel, t.impact, t.category_hint, t.created_at) == \
        ("Laptop will not start", "portal", "cannot_work", "Endpoint / Laptop", NOW)
    with pytest.raises(ValidationError):
        ComplaintIn(**{**COMPLAINT, "subject": "hi"})          # min 3 characters
    sneaky = ComplaintIn(**{**COMPLAINT, "channel": "phone", "created_at": "2020-01-01T00:00:00"})
    assert sneaky.to_ticket().channel == "portal" and sneaky.to_ticket().created_at is None  # not the employee's
    with pytest.raises(ValidationError):
        ComplaintIn(**{**COMPLAINT, "impact": "huge"})


def test_receipt_and_summary_validate_reference_and_status():
    r = ComplaintReceipt(reference="CMP-2026-000123", status="needs_info", submitted_at=NOW,
                         message="Thanks, we need one detail.", question="What is the exact error message?")
    assert r.status == "needs_info" and r.question
    for bad in ({"reference": "cmp 123"}, {"status": "closed"}, {"message": ""}):
        with pytest.raises(ValidationError):
            ComplaintReceipt(**{"reference": "CMP-2026-000123", "status": "received", "submitted_at": NOW,
                                "message": "Received.", **bad})
    s = ComplaintSummary(reference="CMP-2026-000123", subject="Laptop will not start", status="resolved",
                         submitted_at=NOW, resolved_at=NOW,
                         timeline=[TimelineEvent(event="Received", at=NOW), {"event": "Resolved", "at": NOW}])
    assert [e.event for e in s.timeline] == ["Received", "Resolved"]
    assert ComplaintSummary.model_validate_json(s.model_dump_json()) == s
    assert ComplaintSummary(reference="CMP-2026-000124", subject="x", status="received",
                            submitted_at=NOW).timeline == []
