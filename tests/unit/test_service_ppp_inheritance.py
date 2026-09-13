# A newly created service must inherit the mentor's fair-pricing (PPP) setting.
#
# Both creation paths used to leave is_ppp at False while mentors.smart_pricing defaults to TRUE,
# so a mentor with fair pricing on ended up with new sessions that silently opted out of it and
# were priced differently from every other session they offer.
#
# _sync_services_ppp (db/mentors.py) already keeps is_ppp equal to smart_pricing on every toggle;
# creation was the one path that escaped that invariant. These tests pin it shut.
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import db
from routers.services import ServiceCreateBody, create_service


def _mentor(**over):
    base = {"id": "mentor-1", "currency": "USD", "status": "approved"}
    base.update(over)
    return base


# ── the data layer derives it ─────────────────────────────────────────────────

@pytest.mark.parametrize("smart_pricing", [True, False])
def test_create_service_inherits_the_mentors_setting(smart_pricing):
    with patch.object(db.direct_booking, "_mentor_smart_pricing", return_value=smart_pricing) as lookup, \
         patch.object(db.direct_booking, "_supabase") as sb:
        sb.rpc.return_value.execute.return_value.data = "svc-1"
        db.create_service(mentor_id="mentor-1", title="Visa guidance", duration=60, set_price=50)

    lookup.assert_called_once_with("mentor-1")
    assert sb.rpc.call_args.args[1]["p_ppp"] is smart_pricing


def test_an_explicit_value_still_wins():
    """The migration script imports historical rows and must be able to state is_ppp outright."""
    with patch.object(db.direct_booking, "_mentor_smart_pricing") as lookup, \
         patch.object(db.direct_booking, "_supabase") as sb:
        sb.rpc.return_value.execute.return_value.data = "svc-1"
        db.create_service(mentor_id="mentor-1", title="Imported", is_ppp=False)

    lookup.assert_not_called()
    assert sb.rpc.call_args.args[1]["p_ppp"] is False


def test_an_unreadable_mentor_falls_back_to_on_not_off():
    """mentors.smart_pricing defaults to TRUE, so True matches the rest of their sessions.
    Guessing False would recreate the exact bug this prevents."""
    with patch.object(db.direct_booking, "_supabase") as sb:
        sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.side_effect = \
            RuntimeError("db is unhappy")
        assert db.direct_booking._mentor_smart_pricing("mentor-1") is True


def test_reads_the_flag_off_the_mentor_row():
    with patch.object(db.direct_booking, "_supabase") as sb:
        sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = \
            [{"smart_pricing": False}]
        assert db.direct_booking._mentor_smart_pricing("mentor-1") is False


# ── the client cannot set it ──────────────────────────────────────────────────

def test_the_api_does_not_accept_is_ppp_from_the_client():
    """Fair pricing is a property of the mentor, not of one session. A browser posting is_ppp must
    not be able to opt a session out."""
    body = ServiceCreateBody(title="Visa guidance", duration=60, set_price=50, is_ppp=True)
    assert not hasattr(body, "is_ppp")


def test_create_endpoint_never_forwards_an_is_ppp():
    body = ServiceCreateBody(title="Visa guidance", duration=60, set_price=50)
    with patch.object(db, "list_services", return_value=[]), \
         patch.object(db, "create_service", return_value="svc-1") as creator:
        result = create_service(body, mentor=_mentor())

    assert result == {"id": "svc-1"}
    # Omitted entirely, so db.create_service derives it from the mentor.
    assert "is_ppp" not in creator.call_args.kwargs


def test_onboarding_path_also_derives_it():
    """routers/mentor.py creates the signup services without naming is_ppp - same derivation."""
    import inspect
    from routers import mentor as mentor_router
    src = inspect.getsource(mentor_router.mentor_signup)
    assert "is_ppp" not in src


def test_a_second_free_session_is_still_rejected():
    """Guard rail next door - make sure removing the field left it intact."""
    body = ServiceCreateBody(title="Another free one", duration=30, set_price=0)
    with patch.object(db, "list_services", return_value=[{"set_price": 0}]), \
         pytest.raises(HTTPException) as exc:
        create_service(body, mentor=_mentor())
    assert exc.value.status_code == 422
