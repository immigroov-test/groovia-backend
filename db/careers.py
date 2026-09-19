from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def get_career_settings() -> dict[str, Any]:
    row = (_supabase.table("career_settings").select("apply_url")
           .eq("id", 1).maybe_single().execute()).data
    return row or {"apply_url": ""}


def list_career_roles(*, active_only: bool = False) -> list[dict[str, Any]]:
    query = _supabase.table("career_roles").select("*")
    if active_only:
        query = query.eq("is_active", True)
    return (query.order("department_order").order("department").order("sort_order")
            .order("created_at").execute()).data or []


def public_careers() -> dict[str, Any]:
    return {"settings": get_career_settings(), "roles": list_career_roles(active_only=True)}


def admin_careers() -> dict[str, Any]:
    return {"settings": get_career_settings(), "roles": list_career_roles()}


def save_career_settings(apply_url: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return (_supabase.table("career_settings")
            .upsert({"id": 1, "apply_url": apply_url, "updated_at": now})
            .execute()).data[0]


def create_career_role(fields: dict[str, Any]) -> dict[str, Any]:
    return _supabase.table("career_roles").insert(fields).execute().data[0]


def get_career_role(role_id: str) -> Optional[dict[str, Any]]:
    return (_supabase.table("career_roles").select("*").eq("id", role_id)
            .maybe_single().execute()).data


def update_career_role(role_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    payload = {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}
    return (_supabase.table("career_roles").update(payload).eq("id", role_id)
            .execute()).data[0]


def delete_career_role(role_id: str) -> None:
    _supabase.table("career_roles").delete().eq("id", role_id).execute()
