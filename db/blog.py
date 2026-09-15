import re
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

import config

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def _slug(title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:70] or "article"
    return f"{base}-{secrets.token_hex(3)}"


def is_content_contributor(profile_id: str) -> bool:
    row = (_supabase.table("content_contributors").select("profile_id")
           .eq("profile_id", profile_id).eq("active", True).maybe_single().execute()).data
    return bool(row)


def list_content_contributors() -> list[dict]:
    return (_supabase.table("content_contributors")
            .select("profile_id,active,created_at,profiles!content_contributors_profile_id_fkey(email,full_name)")
            .order("created_at", desc=True).execute()).data or []


def set_content_contributor(email: str, active: bool, admin_id: str) -> dict:
    profile = (_supabase.table("profiles").select("id,email,full_name").ilike("email", email.strip())
               .maybe_single().execute()).data
    if not profile:
        raise ValueError("PROFILE_NOT_FOUND")
    row = (_supabase.table("content_contributors").upsert({
        "profile_id": profile["id"], "active": active, "created_by": admin_id,
    }, on_conflict="profile_id").execute()).data[0]
    row["profiles"] = profile
    return row


def list_own_posts(author_id: str) -> list[dict]:
    return (_supabase.table("blog_posts").select("*").eq("author_id", author_id)
            .order("updated_at", desc=True).execute()).data or []


def get_editable_post(post_id: str, author_id: str, is_admin: bool = False) -> Optional[dict]:
    query = _supabase.table("blog_posts").select("*").eq("id", post_id)
    if not is_admin:
        query = query.eq("author_id", author_id)
    return query.maybe_single().execute().data


def create_post(fields: dict[str, Any], author_id: str) -> dict:
    data = {**fields, "author_id": author_id, "slug": _slug(fields["title"]), "status": "draft"}
    return _supabase.table("blog_posts").insert(data).execute().data[0]


def update_post(post_id: str, fields: dict[str, Any]) -> dict:
    data = {**fields, "updated_at": datetime.now(timezone.utc).isoformat()}
    return _supabase.table("blog_posts").update(data).eq("id", post_id).execute().data[0]


def list_admin_posts() -> list[dict]:
    return (_supabase.table("blog_posts").select("*,profiles!blog_posts_author_id_fkey(full_name,email)")
            .order("updated_at", desc=True).execute()).data or []


def list_public_posts(country: Optional[str] = None, category: Optional[str] = None) -> list[dict]:
    query = (_supabase.table("blog_posts")
             .select("id,slug,title,excerpt,cover_image_url,category,country_codes,seo_title,seo_description,last_verified_at,published_at,updated_at,profiles!blog_posts_author_id_fkey(full_name)")
             .eq("status", "published").order("published_at", desc=True))
    if country:
        query = query.contains("country_codes", [country.upper()])
    if category:
        query = query.eq("category", category)
    return query.execute().data or []


def get_public_post(slug: str) -> Optional[dict]:
    return (_supabase.table("blog_posts")
            .select("id,slug,title,excerpt,content_html,cover_image_url,category,country_codes,seo_title,seo_description,sources,last_verified_at,published_at,updated_at,profiles!blog_posts_author_id_fkey(full_name)")
            .eq("slug", slug).eq("status", "published").maybe_single().execute()).data


def record_event(post_id: str, event_type: str, cta_kind: Optional[str]) -> None:
    _supabase.table("blog_events").insert({"post_id": post_id, "event_type": event_type, "cta_kind": cta_kind}).execute()


def analytics_summary() -> list[dict]:
    posts = list_admin_posts()
    events = (_supabase.table("blog_events").select("post_id,event_type,cta_kind").execute()).data or []
    counts: dict[str, dict[str, int]] = {}
    for event in events:
        bucket = counts.setdefault(event["post_id"], {"views": 0, "cta_clicks": 0})
        bucket["views" if event["event_type"] == "view" else "cta_clicks"] += 1
    for post in posts:
        post.update(counts.get(post["id"], {"views": 0, "cta_clicks": 0}))
    return posts
