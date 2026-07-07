# tests/unit/test_pricing_router.py
# HTTP-layer acceptance tests for routers/pricing.py. The pricing MATH itself
# (PPP, FX, fee) lives in the compute_booking_price/get_booking_quote SQL RPCs
# and is verified separately by tests/sql/pricing_engine_test.sql (pgTAP,
# ported from immigroov's supabase/tests/pricing_test.sql) against a live
# Postgres — these tests only cover request validation, routing, and the
# RPC-error -> HTTP-status mapping, with db.* mocked.
import uuid
from unittest.mock import patch

import db


def test_quote_happy_path(client):
    fake_quote = {
        "quote_id": str(uuid.uuid4()),
        "gross_customer": 3333.33,
        "net_mentor": 34.0,
        "ppp_multiplier": 0.40,
        "customer_currency": "INR",
    }
    with patch.object(db, "get_booking_quote", return_value=fake_quote) as mocked:
        resp = client.get(f"/pricing/quote/{uuid.uuid4()}?country=IN")
    assert resp.status_code == 200
    assert resp.json() == fake_quote
    mocked.assert_called_once()


def test_quote_fx_unavailable_maps_to_503(client):
    with patch.object(db, "get_booking_quote",
                       side_effect=Exception("FX_UNAVAILABLE: no fresh exchange rate for USD->INR")):
        resp = client.get(f"/pricing/quote/{uuid.uuid4()}?country=IN")
    assert resp.status_code == 503


def test_quote_service_not_available_maps_to_404(client):
    with patch.object(db, "get_booking_quote", side_effect=Exception("Service not available")):
        resp = client.get(f"/pricing/quote/{uuid.uuid4()}?country=IN")
    assert resp.status_code == 404


def test_quote_unexpected_error_maps_to_500(client):
    with patch.object(db, "get_booking_quote", side_effect=RuntimeError("boom")):
        resp = client.get(f"/pricing/quote/{uuid.uuid4()}?country=IN")
    assert resp.status_code == 500


def test_quote_rejects_malformed_country(client):
    resp = client.get(f"/pricing/quote/{uuid.uuid4()}?country=INDIA")
    assert resp.status_code == 422


def test_quote_country_is_optional(client):
    with patch.object(db, "get_booking_quote", return_value={"quote_id": "x"}):
        resp = client.get(f"/pricing/quote/{uuid.uuid4()}")
    assert resp.status_code == 200


def test_convert_prices_happy_path_and_from_alias(client):
    fake_rows = [{"key": "svc1", "you": 2833.33, "you0": 3333.33,
                  "customer_currency": "INR", "fx_ok": True}]
    with patch.object(db, "convert_prices", return_value=fake_rows) as mocked:
        resp = client.post("/pricing/convert", json={
            "country": "IN",
            "items": [{"key": "svc1", "amount": 100, "from": "USD", "is_ppp": True}],
        })
    assert resp.status_code == 200
    assert resp.json() == {"prices": fake_rows}
    # the "from" JSON alias must reach db.convert_prices as a plain "from" key
    called_items = mocked.call_args.args[1]
    assert called_items[0]["from"] == "USD"
    assert called_items[0]["is_ppp"] is True


def test_convert_prices_rejects_negative_amount(client):
    resp = client.post("/pricing/convert", json={
        "country": "IN",
        "items": [{"key": "x", "amount": -5, "from": "USD"}],
    })
    assert resp.status_code == 422


def test_convert_prices_defaults_from_to_usd_and_is_ppp_false(client):
    with patch.object(db, "convert_prices", return_value=[]) as mocked:
        client.post("/pricing/convert", json={"country": "IN", "items": [{"key": "x", "amount": 10}]})
    called_items = mocked.call_args.args[1]
    assert called_items[0]["from"] == "USD"
    assert called_items[0]["is_ppp"] is False
