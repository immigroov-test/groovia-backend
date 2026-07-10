# tests/unit/test_contact.py
# routers/contact.py — public, unauthenticated contact form. TestClient runs
# BackgroundTasks synchronously, so mailer.send_transactional must be mocked
# or every test would attempt a real Resend call.
from unittest.mock import patch


def _body(**overrides):
    body = {
        "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
        "topic": "General", "message": "Hello there.",
    }
    body.update(overrides)
    return body


def test_submit_contact_happy_path(client):
    with patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post("/contact", json=_body())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    mocked_send.assert_called_once()
    to, template, data = mocked_send.call_args.args
    assert template == "contact_form"
    assert data["email"] == "ada@example.com"


def test_submit_contact_sends_to_admin_email(client):
    with patch("config.ADMIN_EMAIL", "admin@example.com"), \
         patch("services.mailer.send_transactional") as mocked_send:
        client.post("/contact", json=_body())
    to = mocked_send.call_args.args[0]
    assert to == "admin@example.com"


def test_submit_contact_falls_back_to_support_inbox_when_admin_email_unset(client):
    with patch("config.ADMIN_EMAIL", ""), \
         patch("services.mailer.send_transactional") as mocked_send:
        client.post("/contact", json=_body())
    to = mocked_send.call_args.args[0]
    assert to == "support@immigroov.com"


def test_submit_contact_rejects_invalid_email(client):
    with patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post("/contact", json=_body(email="not-an-email"))
    assert resp.status_code == 422
    mocked_send.assert_not_called()


def test_submit_contact_rejects_empty_message(client):
    with patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post("/contact", json=_body(message=""))
    assert resp.status_code == 422
    mocked_send.assert_not_called()


def test_submit_contact_does_not_require_auth(client):
    """No Authorization header at all - this endpoint must stay public."""
    with patch("services.mailer.send_transactional"):
        resp = client.post("/contact", json=_body())
    assert resp.status_code == 200
