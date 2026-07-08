# tests/unit/test_fx_refresh.py
# Ports of immigroov's supabase/functions/fx-refresh Edge Function logic into
# db.pricing.refresh_fx_rates(), plus the admin-triggered stand-in endpoint at
# POST /admin/fx/refresh (see routers/admin.py). No live Postgres/Frankfurter
# call in this environment — httpx and the Supabase client are mocked; these
# tests cover symbol-set building, retry-on-failure, and the upsert/log calls.
import uuid
from unittest.mock import MagicMock, patch

import db
import db.pricing as pricing
from core.auth import AuthUser, get_current_user


def _as_admin(client):
    admin_user = AuthUser(id=str(uuid.uuid4()), email="admin@example.com", role="authenticated")
    client.app.dependency_overrides[get_current_user] = lambda: admin_user
    return admin_user


def _frankfurter_response(rates=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = rates or [
        {"quote": "USD", "rate": 1.08, "date": "2026-07-08"},
        {"quote": "INR", "rate": 90.1, "date": "2026-07-08"},
    ]
    return resp


# ── db.pricing.refresh_fx_rates ─────────────────────────────────────────────

def test_refresh_fx_rates_happy_path():
    fake_table = MagicMock()
    fake_table.select.return_value.eq.return_value.execute.return_value.data = [
        {"set_currency": "eur"},  # lowercase + EUR itself must be excluded from symbols
        {"set_currency": "CAD"},
    ]
    fake_table.upsert.return_value.execute.return_value = None
    fake_table.insert.return_value.execute.return_value = None

    with patch.object(pricing, "_supabase") as mock_supabase, \
         patch("httpx.get", return_value=_frankfurter_response()) as mock_get:
        mock_supabase.table.return_value = fake_table
        result = pricing.refresh_fx_rates()

    assert result["ok"] is True
    assert result["count"] == 2
    mock_get.assert_called_once()
    called_symbols = mock_get.call_args.kwargs["params"]["quotes"]
    assert "CAD" in called_symbols and "EUR" not in called_symbols.split(",")
    fake_table.upsert.assert_called_once()
    insert_call = fake_table.insert.call_args.args[0]
    assert insert_call["success"] is True


def test_refresh_fx_rates_retries_then_succeeds():
    calls = {"n": 0}

    def flaky_get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("network blip")
        return _frankfurter_response()

    fake_table = MagicMock()
    fake_table.select.return_value.eq.return_value.execute.return_value.data = []

    with patch.object(pricing, "_supabase") as mock_supabase, \
         patch("httpx.get", side_effect=flaky_get), \
         patch("time.sleep") as mock_sleep:
        mock_supabase.table.return_value = fake_table
        result = pricing.refresh_fx_rates()

    assert result["ok"] is True
    assert calls["n"] == 2
    mock_sleep.assert_called_once()


def test_refresh_fx_rates_logs_failure_and_raises():
    fake_table = MagicMock()
    fake_table.select.return_value.eq.return_value.execute.return_value.data = []

    with patch.object(pricing, "_supabase") as mock_supabase, \
         patch("httpx.get", side_effect=RuntimeError("frankfurter down")), \
         patch("time.sleep"):
        mock_supabase.table.return_value = fake_table
        try:
            pricing.refresh_fx_rates()
            assert False, "expected refresh_fx_rates to raise"
        except RuntimeError:
            pass

    insert_call = fake_table.insert.call_args.args[0]
    assert insert_call["success"] is False
    assert "frankfurter down" in insert_call["error"]


# ── POST /admin/fx/refresh ───────────────────────────────────────────────────

def test_fx_refresh_endpoint_requires_admin(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="candidate"):
            resp = client.post("/admin/fx/refresh")
        assert resp.status_code == 403
    finally:
        client.app.dependency_overrides.clear()


def test_fx_refresh_endpoint_happy_path(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "refresh_fx_rates", return_value={"ok": True, "as_of": "2026-07-08", "count": 29}) as mocked:
            resp = client.post("/admin/fx/refresh")
        assert resp.status_code == 200
        assert resp.json()["count"] == 29
        mocked.assert_called_once()
    finally:
        client.app.dependency_overrides.clear()


def test_fx_refresh_endpoint_maps_failure_to_502(client):
    _as_admin(client)
    try:
        with patch.object(db, "get_profile_role", return_value="admin"), \
             patch.object(db, "refresh_fx_rates", side_effect=RuntimeError("frankfurter down")):
            resp = client.post("/admin/fx/refresh")
        assert resp.status_code == 502
    finally:
        client.app.dependency_overrides.clear()
