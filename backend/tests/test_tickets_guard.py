"""Step 2 mask: personal data and secrets are hidden in every field; useful IT facts are kept."""
from datetime import datetime, timezone

import pytest

from app.common.redact import find_leaks, mask_text
from app.tickets.guard import mask_ticket
from app.tickets.models import TicketIn


def test_every_text_field_is_masked_and_counted():
    ticket = TicketIn(
        subject="VPN down for priya.s@example.com",
        description=("Call me on +91 98765 43210. My password is Summer2024! and the server is 10.20.30.40. "
                     "Token: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.abc"),
        affected_service="VPN (owner: ops@example.com)",
        environment="laptop",
        channel="phone",
        created_at=datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc),
    )
    masked, counts = mask_ticket(ticket)

    assert counts == {"email": 2, "phone": 1, "password": 1, "ip": 1, "bearer_token": 1}
    dumped = masked.model_dump_json()
    for raw in ("priya.s@", "98765", "Summer2024", "10.20.30.40", "eyJhbGci", "ops@"):
        assert raw not in dumped
    assert find_leaks(dumped) == []
    # Useful facts survive.
    assert "10.20.30.x" in masked.description and "VPN" in masked.subject
    assert masked.created_at == ticket.created_at and masked.environment == "laptop"


def test_input_ticket_is_not_changed():
    ticket = TicketIn(subject="Help", description="mail me at a@b.com")
    mask_ticket(ticket)
    assert ticket.description == "mail me at a@b.com"


def test_clean_ticket_comes_back_equal_with_no_counts():
    ticket = TicketIn(subject="VPN won't connect, error 809", description="Fails since 2026-11-17 09:30 on INC10432.")
    masked, counts = mask_ticket(ticket)
    assert masked == ticket and counts == {}


def test_long_masked_value_never_breaks_the_model():
    ticket = TicketIn(subject="pwd=x1 " + "a" * 290, description="d")
    masked, counts = mask_ticket(ticket)
    assert "<PASSWORD>" in masked.subject and counts == {"password": 1}


@pytest.mark.parametrize("text,expected", [
    ("call me on +91 98765 43210", "call me on <PHONE>"),
    ("phone 98765-43210", "phone <PHONE>"),
    ("(555) 123-4567", "<PHONE>"),
    ("password: hunter", "password: <PASSWORD>"),
    ("pwd=abc123, then retry", "pwd=<PASSWORD>, then retry"),
    ("PIN is 4321.", "PIN is <PASSWORD>."),
])
def test_phone_and_password_are_masked(text, expected):
    assert mask_text(text)[0] == expected


@pytest.mark.parametrize("text", [
    "my password is expired.",
    "the password was reset yesterday",
    "Password reset steps",
    "error 809 at 2026-11-17 10:51",
    "INC10432 and KB5034441",
    "build 1.2.3.4.5 on port 443",
    "ticket 1234567",
])
def test_normal_it_text_is_kept(text):
    assert mask_text(text) == (text, {})


def test_masked_forms_are_not_reported_as_leaks():
    assert find_leaks("password: <PASSWORD>, call <PHONE>") == []
    assert set(find_leaks("password: hunter2, call +91 98765 43210")) == {"password", "phone"}


def test_phone_at_end_of_sentence_is_masked():
    assert mask_text("Call me on 98765 43210.") == ("Call me on <PHONE>.", {"phone": 1})
