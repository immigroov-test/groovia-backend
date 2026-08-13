import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.db.jobs")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)


def job_last_run(job_name: str) -> Optional[datetime]:
    res = _supabase.table("job_run_history").select("last_run_at").eq("job_name", job_name).limit(1).execute()
    if not res.data:
        return None
    return datetime.fromisoformat(res.data[0]["last_run_at"])


def job_is_due(job_name: str, min_interval: timedelta) -> bool:
    last_run = job_last_run(job_name)
    if last_run is None:
        return True
    return datetime.now(timezone.utc) - last_run >= min_interval


def mark_job_run(job_name: str) -> None:
    _supabase.table("job_run_history").upsert({
        "job_name": job_name, "last_run_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
