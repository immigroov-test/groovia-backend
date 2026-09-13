"""FEAT-038: every template must declare which sending identity it goes out from.

This exists because an unmapped template is silent. It still sends, just from the wrong
domain, and the damage (a customer-facing email inheriting the alert stream's reputation,
or a complaint-prone one sharing a domain with password resets) shows up as a delivery
problem weeks later with nothing pointing back at the cause. A new template added without
a stream should fail here, not in the inbox.
"""
import pytest

from services import mailer

PROTECTED = {"security", "bookings"}


def test_every_template_declares_a_stream():
    unmapped = sorted(set(mailer._TEMPLATES) - set(mailer._STREAMS))
    assert not unmapped, (
        f"templates with no sending stream: {unmapped}. Add each to services.mailer._STREAMS. "
        "Use 'alerts' only for mail that never reaches a customer."
    )


def test_no_stream_entry_without_a_template():
    """A leftover mapping is a rename that was only half done."""
    orphans = sorted(set(mailer._STREAMS) - set(mailer._TEMPLATES))
    assert not orphans, f"_STREAMS names templates that do not exist: {orphans}"


def test_streams_resolve_to_a_configured_sender():
    for template in mailer._TEMPLATES:
        sender = mailer._from_for(template)
        assert sender and "@" in sender, f"{template} resolved to a non-address: {sender!r}"


@pytest.mark.parametrize("template,expected", [
    ("auth_recovery", "security"),
    ("auth_magic_link", "security"),
    ("booking_confirmed_candidate", "bookings"),
    ("refund_issued", "bookings"),
    ("review_request", "updates"),
    ("fx_stale_alert", "alerts"),
    ("data_subject_request", "alerts"),
    ("legal_document_updated", "account"),
])
def test_known_templates_keep_their_stream(template, expected):
    """Pins the classifications the split exists for: the two that must never be filtered,
    the one a recipient might report, and internal-only mail."""
    assert mailer._STREAMS.get(template) == expected


def test_customer_facing_mail_is_not_on_the_alert_stream():
    """The alert stream is internal. Anything a customer or mentor receives must not sit on
    it, or their mail inherits a domain nobody protects."""
    customer_facing = {
        "welcome_candidate", "welcome_mentor", "review_request", "legal_document_updated",
        "booking_confirmed_candidate", "booking_confirmed_mentor", "payment_failed",
        "refund_issued", "payout_paid", "mentor_approved", "mentor_rejected",
    }
    on_alerts = sorted(t for t in customer_facing if mailer._STREAMS.get(t) == "alerts")
    assert not on_alerts, f"customer-facing mail on the internal alert stream: {on_alerts}"
