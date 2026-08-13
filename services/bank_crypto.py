"""Application-level encryption for mentor bank details.

Bank account numbers are stored encrypted at rest: the DB only ever holds ciphertext plus a
non-secret last-4 for display. We encrypt in the app (not in Postgres) so the raw values never
travel to or sit in the database in clear, and so the key lives outside the DB (env var, ideally
a KMS later). Fernet = AES-128-CBC + HMAC-SHA256 authenticated encryption from `cryptography`.

Set BANK_ENC_KEY to the output of:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Rotation: BANK_ENC_KEY may hold several keys separated by commas. The FIRST key encrypts new
values; all keys can decrypt (so you add a new key to the front, re-save, then drop the old one).
"""
import logging

from cryptography.fernet import Fernet, MultiFernet, InvalidToken

import config

logger = logging.getLogger("immigroov.bank_crypto")

_cipher: MultiFernet | None = None


def _get_cipher() -> MultiFernet:
    global _cipher
    if _cipher is not None:
        return _cipher
    raw = (config.BANK_ENC_KEY or "").strip()
    if not raw:
        raise RuntimeError(
            "BANK_ENC_KEY is not set - cannot store or read mentor bank details. Generate one with "
            "`python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"` "
            "and set it in the environment."
        )
    keys = [Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()]
    if not keys:
        raise RuntimeError("BANK_ENC_KEY is set but contains no valid key")
    _cipher = MultiFernet(keys)
    return _cipher


def encrypt(plaintext: str) -> str:
    """Encrypt a UTF-8 string, returning a Fernet token (str)."""
    return _get_cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a Fernet token back to the original string. Raises on tampering/wrong key."""
    try:
        return _get_cipher().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise RuntimeError("Could not decrypt bank details (wrong BANK_ENC_KEY or corrupted data)") from e


def is_configured() -> bool:
    """True when a usable key is present, so callers can fail fast with a clear message."""
    try:
        _get_cipher()
        return True
    except Exception:
        return False
