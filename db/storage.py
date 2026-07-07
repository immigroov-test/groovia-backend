import logging

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.storage")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

MENTOR_PHOTO_BUCKET = "mentor-photos"
_bucket_ready = False


def _ensure_photo_bucket() -> None:
    """Create the public mentor-photos bucket on first use so uploads never fail
    with 'Bucket not found'. Uses the service-role client (bypasses RLS)."""
    global _bucket_ready
    if _bucket_ready:
        return
    try:
        _supabase.storage.get_bucket(MENTOR_PHOTO_BUCKET)
        _bucket_ready = True
        return
    except Exception:
        pass
    try:
        _supabase.storage.create_bucket(MENTOR_PHOTO_BUCKET, options={"public": True})
    except Exception:
        # Most likely it already exists (created concurrently). Leave it be.
        logger.info("create_bucket(%s) skipped (may already exist)", MENTOR_PHOTO_BUCKET)
    _bucket_ready = True


def upload_mentor_photo(user_id: str, data: bytes, content_type: str = "image/jpeg") -> str:
    """Store avatar bytes under the user's folder and return the public URL."""
    _ensure_photo_bucket()
    path = f"{user_id}/avatar.jpg"
    _supabase.storage.from_(MENTOR_PHOTO_BUCKET).upload(
        path,
        data,
        {"content-type": content_type, "upsert": "true", "cache-control": "3600"},
    )
    return _supabase.storage.from_(MENTOR_PHOTO_BUCKET).get_public_url(path)
