import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from supabase import Client, create_client

import config

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "webinar"
    return f"{base}-{secrets.token_hex(3)}"


def list_public_webinars() -> list[dict]:
    rows = (_supabase.table("webinars").select("id,slug,title,description,banner_url,media_url,starts_at,duration_minutes,timezone,capacity,is_paid,price,currency,mentor_id")
            .eq("status", "published").gte("starts_at", datetime.now(timezone.utc).isoformat()).order("starts_at").execute()).data or []
    return _with_counts(rows)


def get_public_webinar(slug: str) -> Optional[dict]:
    res = (_supabase.table("webinars").select("id,slug,title,description,banner_url,media_url,starts_at,duration_minutes,timezone,capacity,is_paid,price,currency,mentor_id,status")
           .eq("slug", slug).eq("status", "published").maybe_single().execute())
    rows = _with_counts([res.data] if res.data else [])
    return rows[0] if rows else None


def _with_counts(rows: list[dict]) -> list[dict]:
    for row in rows:
        regs = (_supabase.table("webinar_registrations").select("id", count="exact")
                .eq("webinar_id", row["id"]).in_("status", ["confirmed", "attended"]).execute())
        row["registration_count"] = regs.count or 0
        if row.get("mentor_id"):
            mentor = (_supabase.table("mentors").select("display_name,slug,photo_url").eq("id", row["mentor_id"]).maybe_single().execute()).data
            row["mentor"] = mentor
    return rows


def create_webinar(fields: dict[str, Any], *, creator_id: str, source: str = "admin", status: str = "draft") -> dict:
    data = {**fields, "created_by": creator_id, "source": source, "status": status, "slug": _slug(fields["title"])}
    if data.get("meeting_provider") == "jitsi_public" and not data.get("meeting_room"):
        data["meeting_room"] = f"groovia-webinar-{secrets.token_urlsafe(24)}"
    return _supabase.table("webinars").insert(data).execute().data[0]


def update_webinar(webinar_id: str, fields: dict[str, Any]) -> dict:
    fields = {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}
    return _supabase.table("webinars").update(fields).eq("id", webinar_id).execute().data[0]


def list_admin_webinars() -> list[dict]:
    rows = _supabase.table("webinars").select("*").order("created_at", desc=True).execute().data or []
    return _with_counts(rows)


def list_mentor_webinars(mentor_id: str) -> list[dict]:
    return (_supabase.table("webinars").select("*").eq("mentor_id", mentor_id).order("created_at", desc=True).execute()).data or []


def get_webinar(webinar_id: str) -> Optional[dict]:
    return (_supabase.table("webinars").select("*").eq("id", webinar_id).maybe_single().execute()).data


def register(webinar_id: str, user_id: str, paid: bool) -> dict:
    fn = "webinar_reserve_paid" if paid else "webinar_confirm_free"
    return _supabase.rpc(fn, {"p_webinar_id": webinar_id, "p_user_id": user_id}).execute().data


def my_registrations(user_id: str) -> list[dict]:
    rows = (_supabase.table("webinar_registrations").select("*,webinars(id,slug,title,starts_at,duration_minutes,timezone,status,is_paid)")
            .eq("user_id", user_id).order("created_at", desc=True).execute()).data or []
    return rows


def registration_for(webinar_id: str, user_id: str) -> Optional[dict]:
    return (_supabase.table("webinar_registrations").select("*").eq("webinar_id", webinar_id).eq("user_id", user_id).maybe_single().execute()).data


def registration_for_id(registration_id: str) -> Optional[dict]:
    return (_supabase.table("webinar_registrations").select("*").eq("id", registration_id).maybe_single().execute()).data


def webinar_registration_by_order(order_id: str) -> Optional[dict]:
    return (_supabase.table("webinar_registrations").select("*").eq("provider_order_id", order_id).maybe_single().execute()).data


def finalize_webinar_payment(registration: dict, remote: dict) -> dict:
    """Confirm only a captured payment whose immutable amount/currency/order match our seat."""
    expected_minor = round(float(registration["amount"]) * (1 if registration["currency"] in ("JPY", "KRW", "VND") else 100))
    if (remote.get("status") != "captured" or remote.get("order_id") != registration.get("provider_order_id")
            or int(remote.get("amount") or -1) != expected_minor
            or str(remote.get("currency") or "").upper() != str(registration["currency"]).upper()):
        raise ValueError("PAYMENT_MISMATCH")
    return (_supabase.table("webinar_registrations").update({
        "status": "confirmed", "provider_payment_id": remote["id"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", registration["id"]).execute()).data[0]


def fail_webinar_payment(registration_id: str) -> None:
    _supabase.table("webinar_registrations").update({"status": "cancelled", "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", registration_id).eq("status", "pending_payment").execute()


def create_razorpay_order(registration: dict) -> dict:
    amount = round(float(registration["amount"]) * (1 if registration["currency"] in ("JPY", "KRW", "VND") else 100))
    payload = {"amount": amount, "currency": registration["currency"], "receipt": f"webinar_{registration['id']}", "notes": {"registration_id": registration["id"]}}
    if config.MOCK_SERVICES:
        order = {"id": f"order_mock_{secrets.token_hex(8)}", **payload}
    else:
        res = httpx.post("https://api.razorpay.com/v1/orders", json=payload, auth=(config.RAZORPAY_KEY_ID, config.RAZORPAY_KEY_SECRET), timeout=15)
        res.raise_for_status()
        order = res.json()
    _supabase.table("webinar_registrations").update({"provider": "razorpay", "provider_order_id": order["id"], "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", registration["id"]).execute()
    return order


def confirm_razorpay(registration_id: str, order_id: str, payment_id: str, signature: str) -> dict:
    reg = (_supabase.table("webinar_registrations").select("*").eq("id", registration_id).maybe_single().execute()).data
    if not reg or reg.get("provider_order_id") != order_id:
        raise ValueError("INVALID_ORDER")
    if reg.get("status") == "confirmed" and reg.get("provider_payment_id") == payment_id:
        return reg
    expected = hmac.new(config.RAZORPAY_KEY_SECRET.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    if not config.MOCK_SERVICES and not hmac.compare_digest(expected, signature or ""):
        raise ValueError("INVALID_SIGNATURE")
    return (_supabase.table("webinar_registrations").update({"status": "confirmed", "provider_payment_id": payment_id, "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", registration_id).execute()).data[0]


def join_details(webinar_id: str, user_id: str, is_admin: bool = False, mentor_id: Optional[str] = None) -> Optional[dict]:
    webinar = get_webinar(webinar_id)
    if not webinar or webinar["status"] not in ("published", "live"):
        return None
    allowed = is_admin or (mentor_id and webinar.get("mentor_id") == mentor_id)
    if not allowed:
        reg = registration_for(webinar_id, user_id)
        allowed = bool(reg and reg.get("status") in ("confirmed", "attended"))
    if not allowed:
        return None
    now = datetime.now(timezone.utc)
    starts = datetime.fromisoformat(webinar["starts_at"].replace("Z", "+00:00"))
    if now < starts - timedelta(minutes=15):
        raise ValueError("JOIN_TOO_EARLY")
    if now > starts + timedelta(minutes=int(webinar["duration_minutes"]) + 30):
        raise ValueError("JOIN_CLOSED")
    meeting_url = webinar.get("meeting_url")
    if webinar["meeting_provider"] == "jitsi_public":
        meeting_url = f"https://meet.jit.si/{webinar.get('meeting_room')}"
    return {"id": webinar["id"], "title": webinar["title"], "meeting_provider": webinar["meeting_provider"], "meeting_url": meeting_url}


def record_attendance(webinar_id: str, user_id: str, event: str) -> None:
    field = "joined_at" if event == "join" else "left_at"
    values = {field: datetime.now(timezone.utc).isoformat(), "updated_at": datetime.now(timezone.utc).isoformat()}
    if event == "join": values["status"] = "attended"
    _supabase.table("webinar_registrations").update(values).eq("webinar_id", webinar_id).eq("user_id", user_id).execute()
