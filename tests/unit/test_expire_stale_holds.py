# tests/unit/test_expire_stale_holds.py
# HTTP-layer acceptance tests for POST /admin/payments/expire-holds, the
# admin-triggered stand-in for immigroov's pg_cron `expire-payment-holds` job
# (see routers/admin.py, db/payments.py, and expire_stale_holds() ported into
# migrations/production_db_setup.sql + testing_db_setup.sql). SQL-layer
# behavior (hold release, payment state flip) is not re-verified here — no
# live Postgres in this environment, same limitation as every prior module —
# these tests cover admin-gating and RPC-error -> HTTP-status mapping with
# db.* mocked.
import uuid
from unittest.mock import patch

import db
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


def test_expire_holds_requires_admin(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.post("/admin/payments/expire-holds")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_expire_holds_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "expire_stale_holds", return_value=3) as mocked:
            resp = client.post("/admin/payments/expire-holds")
        assert resp.status_code == 200
        assert resp.json() == {"holds_expired": 3}
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_expire_holds_maps_failure_to_502(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "expire_stale_holds", side_effect=RuntimeError("db down")):
            resp = client.post("/admin/payments/expire-holds")
        assert resp.status_code == 502
    finally:
        client.app.dependency_overrides.clear()
