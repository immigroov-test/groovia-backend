"""Mentor bank-account persistence. The only place that reads/writes mentor_bank_accounts.

Sensitive numbers are encrypted (services/bank_crypto) before they ever reach the DB; the
cleartext columns are display-safe metadata. `get_mentor_bank_masked` never decrypts;
`get_mentor_bank_revealed` decrypts for a deliberate admin/founder action.
"""
import json
import logging
from typing import Any, Optional

from .mentors import _supabase
from services import bank_crypto

logger = logging.getLogger("immigroov.db")


def _masked(row: dict) -> dict:
    last4 = (row.get("account_last4") or "").strip()
    return {
        "has_details": True,
        "country_code": row.get("country_code"),
        "scheme": row.get("scheme"),
        "account_holder_name": row.get("account_holder_name"),
        "bank_name": row.get("bank_name"),
        "account_last4": last4,
        "account_masked": (f"•••• {last4}" if last4 else "••••"),
        "updated_at": row.get("updated_at"),
    }


def upsert_mentor_bank(mentor_id: str, validated: dict[str, Any]) -> dict:
    """Encrypt the secret bundle and upsert the mentor's single bank row. Returns the masked view.
    Raises RuntimeError if BANK_ENC_KEY is not configured (surfaced by the caller)."""
    details_enc = bank_crypto.encrypt(json.dumps(validated["secret"], separators=(",", ":")))
    payload = {
        "mentor_id": mentor_id,
        "country_code": validated["country_code"],
        "scheme": validated["scheme"],
        "account_holder_name": validated["account_holder_name"],
        "bank_name": validated.get("bank_name"),
        "account_last4": validated.get("account_last4"),
        "details_enc": details_enc,
    }
    res = _supabase.table("mentor_bank_accounts").upsert(payload, on_conflict="mentor_id").execute()
    return _masked(res.data[0] if res.data else payload)


def get_mentor_bank_masked(mentor_id: str) -> Optional[dict]:
    """Display-safe view (no decryption): holder, bank, country, scheme, last 4."""
    res = (
        _supabase.table("mentor_bank_accounts")
        .select("country_code, scheme, account_holder_name, bank_name, account_last4, updated_at")
        .eq("mentor_id", mentor_id)
        .limit(1)
        .execute()
    )
    return _masked(res.data[0]) if res.data else None


def get_mentor_bank_revealed(mentor_id: str) -> Optional[dict]:
    """Masked view PLUS the decrypted secret numbers under `details`. For a deliberate
    admin/founder reveal only. Raises if decryption fails."""
    res = _supabase.table("mentor_bank_accounts").select("*").eq("mentor_id", mentor_id).limit(1).execute()
    if not res.data:
        return None
    row = res.data[0]
    out = _masked(row)
    out["details"] = json.loads(bank_crypto.decrypt(row["details_enc"]))
    return out


def mentor_has_bank(mentor_id: str) -> bool:
    res = _supabase.table("mentor_bank_accounts").select("mentor_id").eq("mentor_id", mentor_id).limit(1).execute()
    return bool(res.data)
