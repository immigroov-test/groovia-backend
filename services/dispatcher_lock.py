import logging
from contextlib import contextmanager
from typing import Iterator

from supabase import Client, create_client

import config

logger = logging.getLogger("immigroov.services.dispatcher_lock")

_supabase: Client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)

# Guards against Render Cron Jobs having no built-in mutex — if a tick's runtime
# exceeds its schedule interval, Render can start the next tick's container while
# the previous one is still running, and every dispatcher job would risk running
# twice concurrently. TTL-based lease row (not pg_advisory_lock — supabase-py's
# stateless RPC calls can't reliably hold a session-level advisory lock across
# two separate calls).

DEFAULT_TTL_SECONDS = 300  # comfortably longer than one 5-minute dispatcher tick


class LockNotAcquired(Exception):
    """Raised by dispatcher_lock() when another run already holds the lease."""


def try_acquire(lock_name: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    res = _supabase.rpc("try_acquire_dispatcher_lock", {
        "p_lock_name": lock_name, "p_ttl_seconds": ttl_seconds,
    }).execute()
    return bool(res.data)


def release(lock_name: str) -> None:
    _supabase.rpc("release_dispatcher_lock", {"p_lock_name": lock_name}).execute()


@contextmanager
def dispatcher_lock(lock_name: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> Iterator[None]:
    """Wrap a dispatcher tick's whole job loop in this. Raises LockNotAcquired
    immediately (no blocking/retry — a losing tick should just exit) if another
    run currently holds the lease. Always releases on the way out, including when
    the wrapped code raises; the TTL is the backstop for a hard process kill that
    skips even the `finally`."""
    if not try_acquire(lock_name, ttl_seconds):
        logger.warning("dispatcher lock %r already held — skipping this tick", lock_name)
        raise LockNotAcquired(lock_name)
    try:
        yield
    finally:
        release(lock_name)
