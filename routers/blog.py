from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

import db
from core.auth import AuthUser, get_current_user, require_admin
from core.rate_limit import limiter
from services import blog_writer
from services.html_sanitizer import sanitize_html

router = APIRouter(tags=["blog"])

Category = Literal["visas_immigration", "jobs_careers", "study_abroad", "housing", "banking_taxes", "healthcare", "family_relocation", "settling_in", "culture_lifestyle", "stories"]


class StoredImagePrompt(BaseModel):
    prompt: str = Field(min_length=5, max_length=300)
    alt_text: str = Field(min_length=3, max_length=160)
    caption: str = Field(default="", max_length=240)


class StoredCta(BaseModel):
    kind: Literal["mentors", "webinars", "country"]
    label: str = Field(min_length=3, max_length=100)
    href: str = Field(min_length=2, max_length=200)

    @field_validator("href")
    @classmethod
    def internal_href(cls, value: str) -> str:
        if not value.startswith(("/mentors", "/webinars", "/countries/")) or value.startswith("//"):
            raise ValueError("CTA links must point to a supported Groovia page")
        return value


class PostBody(BaseModel):
    title: str = Field(min_length=4, max_length=160)
    excerpt: str = Field(default="", max_length=400)
    content_html: str = Field(default="", max_length=100000)
    content_json: list[dict] = Field(default_factory=list, max_length=1000)
    cover_image_url: Optional[str] = None
    category: Category
    country_codes: list[str] = Field(default_factory=list, max_length=8)
    seo_title: Optional[str] = Field(default=None, max_length=70)
    seo_description: Optional[str] = Field(default=None, max_length=170)
    sources: list[str] = Field(default_factory=list, max_length=20)
    image_prompts: list[StoredImagePrompt] = Field(default_factory=list, max_length=4)
    ctas: list[StoredCta] = Field(default_factory=list, max_length=3)
    last_verified_at: Optional[date] = None

    @field_validator("content_html")
    @classmethod
    def clean_content(cls, value: str) -> str:
        return sanitize_html(value)

    @field_validator("country_codes")
    @classmethod
    def countries(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(v.strip().upper() for v in values if v.strip()))
        if any(len(v) != 2 or not v.isalpha() for v in cleaned):
            raise ValueError("Countries must use two-letter ISO codes")
        return cleaned

    @field_validator("cover_image_url")
    @classmethod
    def image_url(cls, value: Optional[str]) -> Optional[str]:
        if not value or not value.strip():
            return None
        value = value.strip()
        if not value.startswith("https://"):
            raise ValueError("Cover image must use HTTPS")
        return value

    @field_validator("sources")
    @classmethod
    def source_urls(cls, values: list[str]) -> list[str]:
        cleaned = [v.strip() for v in values if v.strip()]
        if any(not v.startswith("https://") for v in cleaned):
            raise ValueError("Sources must use HTTPS URLs")
        return cleaned


def _require_contributor(user: AuthUser) -> bool:
    is_admin = db.get_profile_role(user.id) == "admin"
    if not is_admin and not db.is_content_contributor(user.id):
        raise HTTPException(status_code=403, detail="Content contributor access required")
    return is_admin


@router.get("/content/access")
def contributor_access(user: AuthUser = Depends(get_current_user)):
    return {"allowed": _require_contributor(user), "admin": db.get_profile_role(user.id) == "admin"}


@router.get("/content/posts")
def own_posts(user: AuthUser = Depends(get_current_user)):
    _require_contributor(user)
    return db.list_own_posts(user.id)


class GenerateBody(BaseModel):
    raw_content: str = Field(min_length=20, max_length=30000)
    country_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    category: Category
    tone: Literal["clear", "friendly", "professional", "inspiring"] = "clear"
    sources: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("country_code")
    @classmethod
    def country(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip().upper()
        if len(value) != 2 or not value.isalpha():
            raise ValueError("Country must use a two-letter ISO code")
        return value

    @field_validator("sources")
    @classmethod
    def source_urls(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(not v.startswith("https://") for v in cleaned):
            raise ValueError("Sources must use HTTPS URLs")
        return cleaned


@router.post("/content/generate")
@limiter.limit("5/minute")
async def generate_post(request: Request, body: GenerateBody, user: AuthUser = Depends(get_current_user)):
    _require_contributor(user)
    try:
        return await blog_writer.generate_article(
            raw_content=body.raw_content,
            country_code=body.country_code,
            category=body.category,
            tone=body.tone,
            sources=body.sources,
            related_posts=db.related_posts({
                "id": "",
                "country_codes": [body.country_code] if body.country_code else [],
                "category": body.category,
            }, limit=8),
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        if status == 429 or getattr(getattr(exc, "response", None), "status_code", None) == 429:
            raise HTTPException(status_code=429, detail="The AI writer is busy. Please try again shortly.")
        raise HTTPException(status_code=502, detail="The AI writer could not create the article. Your notes are safe.")


_IMAGE_TYPES = {
    "image/jpeg": (b"\xff\xd8\xff", {".jpg", ".jpeg"}),
    "image/png": (b"\x89PNG\r\n\x1a\n", {".png"}),
    "image/gif": (b"GIF8", {".gif"}),
    "image/webp": (b"RIFF", {".webp"}),
}


@router.post("/content/media")
async def upload_media(file: UploadFile = File(...), user: AuthUser = Depends(get_current_user)):
    _require_contributor(user)
    content_type = (file.content_type or "").lower()
    filename = (file.filename or "image").lower()
    expected = _IMAGE_TYPES.get(content_type)
    if not expected or not any(filename.endswith(ext) for ext in expected[1]):
        raise HTTPException(status_code=415, detail="Upload a JPG, PNG, WebP, or GIF image.")
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Images must be 5 MB or smaller.")
    if not content.startswith(expected[0]) or (content_type == "image/webp" and content[8:12] != b"WEBP"):
        raise HTTPException(status_code=415, detail="The file content does not match its image type.")
    return {"url": db.upload_blog_image(user.id, filename, content, content_type)}


@router.post("/content/posts")
def create_post(body: PostBody, user: AuthUser = Depends(get_current_user)):
    _require_contributor(user)
    return db.create_post(body.model_dump(mode="json"), user.id)


@router.get("/content/posts/{post_id}")
def editable_post(post_id: str, user: AuthUser = Depends(get_current_user)):
    is_admin = _require_contributor(user)
    post = db.get_editable_post(post_id, user.id, is_admin)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


@router.patch("/content/posts/{post_id}")
def save_post(post_id: str, body: PostBody, user: AuthUser = Depends(get_current_user)):
    is_admin = _require_contributor(user)
    post = db.get_editable_post(post_id, user.id, is_admin)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not is_admin and post["status"] not in ("draft", "changes_requested"):
        raise HTTPException(status_code=409, detail="Submitted posts cannot be edited")
    return db.update_post(post_id, body.model_dump(mode="json"))


@router.post("/content/posts/{post_id}/submit")
def submit_post(post_id: str, user: AuthUser = Depends(get_current_user)):
    post = db.get_editable_post(post_id, user.id, _require_contributor(user))
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if not post.get("excerpt") or not post.get("content_html") or not post.get("country_codes") or not post.get("sources"):
        raise HTTPException(status_code=422, detail="Complete the article, country, excerpt, and sources before submitting")
    return db.update_post(post_id, {"status": "submitted", "review_note": None})


@router.get("/admin/blog-posts")
def admin_posts(user: AuthUser = Depends(require_admin)):
    return db.analytics_summary()


@router.get("/admin/content-contributors")
def admin_contributors(user: AuthUser = Depends(require_admin)):
    return db.list_content_contributors()


class ContributorBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    active: bool = True


@router.post("/admin/content-contributors")
def admin_set_contributor(body: ContributorBody, user: AuthUser = Depends(require_admin)):
    try:
        return db.set_content_contributor(body.email, body.active, user.id)
    except ValueError:
        raise HTTPException(status_code=404, detail="No Immigroov account uses that email")


class ReviewBody(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


@router.post("/admin/blog-posts/{post_id}/{action}")
def review_post(post_id: str, action: Literal["publish", "request-changes", "archive"], body: ReviewBody = ReviewBody(), user: AuthUser = Depends(require_admin)):
    post = db.get_editable_post(post_id, user.id, True)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    if action == "publish" and post["status"] not in ("submitted", "changes_requested"):
        raise HTTPException(status_code=409, detail="Only reviewed submissions can be published")
    fields = {"reviewed_by": user.id, "review_note": body.note}
    if action == "publish":
        fields.update({"status": "published", "published_at": datetime.now(timezone.utc).isoformat()})
    elif action == "request-changes":
        fields["status"] = "changes_requested"
    else:
        fields["status"] = "archived"
    return db.update_post(post_id, fields)


@router.get("/blog")
def public_posts(country: Optional[str] = Query(default=None), category: Optional[str] = Query(default=None)):
    return db.list_public_posts(country, category)


@router.get("/blog/{slug}")
def public_post(slug: str):
    post = db.get_public_post(slug)
    if not post:
        raise HTTPException(status_code=404, detail="Article not found")
    return {**post, "related_posts": db.related_posts(post)}


class EventBody(BaseModel):
    event_type: Literal["view", "cta_click"]
    cta_kind: Optional[Literal["mentors", "webinars", "country"]] = None


@router.post("/blog/{post_id}/events", status_code=202)
def track_event(post_id: str, body: EventBody):
    db.record_event(post_id, body.event_type, body.cta_kind)
    return {"ok": True}
