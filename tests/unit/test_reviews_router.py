# tests/unit/test_reviews_router.py
# HTTP-layer acceptance tests for routers/reviews.py + the review-adjacent
# endpoints added to routers/mentors.py and routers/admin.py. The SQL business
# rules (star-gating, one-review-per-booking, rating rollup trigger) live in
# the RPCs/triggers ported in the Reviews migration commits and are NOT
# re-verified here (no live Postgres in this environment, consistent with
# Pricing+PPP and Payments) — these tests cover request validation, routing,
# and RPC-error -> HTTP-status mapping, with db.* mocked.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    """Override the auth dependency so require_admin's JWT decode is skipped;
    db.get_profile_role still needs mocking per-test to actually grant admin."""
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


# ── GET /reviews/token/{token} ──────────────────────────────────────────────

def test_token_info_happy_path(client):
    fake_info = {"booking_id": str(uuid.uuid4()), "mentor_name": "Aya", "service_title": "Career chat",
                 "expired": False, "already_submitted": False, "rating": None}
    with patch.object(db, "get_review_token_info", return_value=fake_info):
        resp = client.get(f"/reviews/token/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json() == fake_info


def test_token_info_invalid_token_maps_to_404(client):
    with patch.object(db, "get_review_token_info", side_effect=Exception("Invalid review link")):
        resp = client.get(f"/reviews/token/{uuid.uuid4()}")
    assert resp.status_code == 404


# ── POST /reviews/submit ─────────────────────────────────────────────────────

def test_submit_happy_path_no_email_for_low_rating(client):
    with patch.object(db, "submit_review", return_value={"review_id": str(uuid.uuid4()), "status": "published", "rating": 4}) as mocked, \
         patch.object(db, "get_review_token_info") as mocked_lookup:
        resp = client.post("/reviews/submit", json={
            "token": str(uuid.uuid4()), "rating": 4, "title": "Great session", "review": "Really helpful.",
        })
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"
    mocked.assert_called_once()
    mocked_lookup.assert_not_called()  # no 5-star notification path taken


def test_submit_five_star_triggers_notification(client):
    booking_id = str(uuid.uuid4())
    with patch.object(db, "submit_review", return_value={"review_id": str(uuid.uuid4()), "status": "published", "rating": 5}), \
         patch.object(db, "get_review_token_info", return_value={"booking_id": booking_id}), \
         patch.object(db, "get_review_notify_info", return_value={"mentor_name": "Aya", "mentor_email": "aya@example.com"}) as mocked_notify, \
         patch("services.mailer.send_transactional") as mocked_send:
        resp = client.post("/reviews/submit", json={
            "token": str(uuid.uuid4()), "rating": 5, "title": "Amazing!", "review": "10/10.",
        })
    assert resp.status_code == 200
    mocked_notify.assert_called_once_with(booking_id)
    mocked_send.assert_called_once()
    assert mocked_send.call_args.args[0] == "aya@example.com"
    assert mocked_send.call_args.args[1] == "mentor_five_star_review"


def test_submit_rejects_out_of_range_rating(client):
    resp = client.post("/reviews/submit", json={"token": str(uuid.uuid4()), "rating": 6})
    assert resp.status_code == 422


def test_submit_expired_token_maps_to_409(client):
    with patch.object(db, "submit_review", side_effect=Exception("This review link has expired")):
        resp = client.post("/reviews/submit", json={"token": str(uuid.uuid4()), "rating": 3})
    assert resp.status_code == 409


def test_submit_already_used_token_maps_to_409(client):
    with patch.object(db, "submit_review", side_effect=Exception("A review was already submitted for this session")):
        resp = client.post("/reviews/submit", json={"token": str(uuid.uuid4()), "rating": 3})
    assert resp.status_code == 409


def test_submit_not_completed_booking_maps_to_400(client):
    with patch.object(db, "submit_review", side_effect=Exception("You can only review a completed session")):
        resp = client.post("/reviews/submit", json={"token": str(uuid.uuid4()), "rating": 3})
    assert resp.status_code == 400


def test_submit_title_and_review_are_trimmed(client):
    with patch.object(db, "submit_review", return_value={"review_id": "x", "status": "pending", "rating": 2}) as mocked:
        client.post("/reviews/submit", json={
            "token": str(uuid.uuid4()), "rating": 2, "title": "  spaced  ", "review": "  also spaced  ",
        })
    _, _, title, review = mocked.call_args.args
    assert title == "spaced"
    assert review == "also spaced"


# ── GET /mentors/{slug}/reviews ──────────────────────────────────────────────

def test_mentor_reviews_happy_path(client):
    mentor = {"id": str(uuid.uuid4()), "slug": "aya-k"}
    fake_result = {"breakdown": {"5": 3, "4": 1, "3": 0, "2": 0, "1": 0}, "reviews": []}
    with patch.object(db, "get_mentor_by_slug", return_value=mentor), \
         patch.object(db, "mentor_reviews_public", return_value=fake_result) as mocked:
        resp = client.get("/mentors/aya-k/reviews")
    assert resp.status_code == 200
    assert resp.json() == fake_result
    mocked.assert_called_once_with(mentor["id"], limit=10, offset=0)


def test_mentor_reviews_unknown_slug_is_404(client):
    with patch.object(db, "get_mentor_by_slug", return_value=None):
        resp = client.get("/mentors/nobody/reviews")
    assert resp.status_code == 404


def test_mentor_reviews_rejects_out_of_range_limit(client):
    resp = client.get("/mentors/aya-k/reviews?limit=100")
    assert resp.status_code == 422


# ── Admin moderation ──────────────────────────────────────────────────────────

def test_pending_reviews_requires_admin_role(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_reviews_queue", return_value=[{"review_id": "x"}]) as mocked:
            resp = client.get("/admin/reviews/pending")
        assert resp.status_code == 200
        assert resp.json() == [{"review_id": "x"}]
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_pending_reviews_rejects_non_admin(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.get("/admin/reviews/pending")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_approve_review_happy_path(client):
    admin_user = _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_moderate_review") as mocked:
            resp = client.post(f"/admin/reviews/{uuid.uuid4()}/approve")
        assert resp.status_code == 200
        assert resp.json() == {"approved": True}
        assert mocked.call_args.args[1] == "approve"
        assert mocked.call_args.args[2] == admin_user.id
    finally:
        client.app.dependency_overrides.clear()


def test_reject_review_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_moderate_review") as mocked:
            resp = client.post(f"/admin/reviews/{uuid.uuid4()}/reject")
        assert resp.status_code == 200
        assert resp.json() == {"rejected": True}
        assert mocked.call_args.args[1] == "reject"
    finally:
        client.app.dependency_overrides.clear()


def test_approve_review_not_pending_maps_to_409(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_moderate_review", side_effect=Exception("Review is not awaiting moderation (status published)")):
            resp = client.post(f"/admin/reviews/{uuid.uuid4()}/approve")
        assert resp.status_code == 409
    finally:
        client.app.dependency_overrides.clear()


def test_approve_review_not_found_maps_to_404(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "admin_moderate_review", side_effect=Exception("Review not found")):
            resp = client.post(f"/admin/reviews/{uuid.uuid4()}/approve")
        assert resp.status_code == 404
    finally:
        client.app.dependency_overrides.clear()
