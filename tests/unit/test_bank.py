# tests/unit/test_bank.py
# Mentor payout bank details: country-aware validation + at-rest encryption.
import pytest
from cryptography.fernet import Fernet

import config
from services import bank_crypto, bank_validation as bv

HOLDER = "Jane Q. Doe"


# ── Encryption ───────────────────────────────────────────────────────────────────

def test_crypto_roundtrip(monkeypatch):
    monkeypatch.setattr(config, "BANK_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(bank_crypto, "_cipher", None)
    token = bank_crypto.encrypt("50100123456")
    assert token != "50100123456"                     # not stored in clear
    assert bank_crypto.decrypt(token) == "50100123456"
    assert bank_crypto.is_configured() is True


def test_crypto_requires_key(monkeypatch):
    monkeypatch.setattr(config, "BANK_ENC_KEY", "")
    monkeypatch.setattr(bank_crypto, "_cipher", None)
    assert bank_crypto.is_configured() is False
    with pytest.raises(RuntimeError):
        bank_crypto.encrypt("x")


def test_crypto_rotation_decrypts_old(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(config, "BANK_ENC_KEY", old)
    monkeypatch.setattr(bank_crypto, "_cipher", None)
    token = bank_crypto.encrypt("secret")
    # New key first, old key retained -> old ciphertext still decrypts.
    monkeypatch.setattr(config, "BANK_ENC_KEY", f"{new},{old}")
    monkeypatch.setattr(bank_crypto, "_cipher", None)
    assert bank_crypto.decrypt(token) == "secret"


# ── Country -> scheme ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cc,scheme", [
    ("IN", "india"), ("US", "us"), ("GB", "uk"),
    ("DE", "iban"), ("FR", "iban"), ("NL", "iban"),
    ("SG", "swift"), ("AU", "swift"), ("JP", "swift"),
])
def test_scheme_for_country(cc, scheme):
    assert bv.scheme_for_country(cc) == scheme


# ── Valid submissions per scheme ──────────────────────────────────────────────────

def test_valid_iban():
    r = bv.validate_bank_details("DE", {"account_holder_name": HOLDER, "iban": "DE89 3704 0044 0532 0130 00"})
    assert r["scheme"] == "iban"
    assert r["secret"]["iban"] == "DE89370400440532013000"
    assert r["account_last4"] == "3000"


def test_valid_us():
    r = bv.validate_bank_details("US", {
        "account_holder_name": HOLDER, "account_number": "000123456789",
        "routing_number": "021000021", "account_type": "checking",
    })
    assert r["scheme"] == "us"
    assert r["secret"]["routing_number"] == "021000021"
    assert r["secret"]["account_type"] == "checking"
    assert r["account_last4"] == "6789"


def test_valid_india():
    r = bv.validate_bank_details("IN", {
        "account_holder_name": HOLDER, "bank_name": "HDFC Bank",
        "account_number": "50100123456", "ifsc": "hdfc0001234",
    })
    assert r["scheme"] == "india"
    assert r["secret"]["ifsc"] == "HDFC0001234"          # normalized upper
    assert r["bank_name"] == "HDFC Bank"


def test_valid_uk():
    r = bv.validate_bank_details("GB", {
        "account_holder_name": HOLDER, "account_number": "12345678", "sort_code": "12-34-56",
    })
    assert r["scheme"] == "uk"
    assert r["secret"]["sort_code"] == "123456"          # separators stripped


def test_valid_swift_fallback():
    r = bv.validate_bank_details("SG", {
        "account_holder_name": HOLDER, "bank_name": "DBS", "account_number": "0123456789",
        "swift_bic": "dbsssgsg",
    })
    assert r["scheme"] == "swift"
    assert r["secret"]["swift_bic"] == "DBSSSGSG"


# ── Rejections (each on the field under test, with a valid holder name) ────────────

def test_bad_iban_checksum():
    with pytest.raises(ValueError, match="checksum"):
        bv.validate_bank_details("DE", {"account_holder_name": HOLDER, "iban": "DE89370400440532013001"})


def test_iban_wrong_length():
    with pytest.raises(ValueError):
        bv.validate_bank_details("DE", {"account_holder_name": HOLDER, "iban": "DE8937040044"})


def test_bad_us_routing_checksum():
    with pytest.raises(ValueError, match="checksum"):
        bv.validate_bank_details("US", {
            "account_holder_name": HOLDER, "account_number": "123456",
            "routing_number": "123456789", "account_type": "checking",
        })


def test_us_bad_account_type():
    with pytest.raises(ValueError, match="account type"):
        bv.validate_bank_details("US", {
            "account_holder_name": HOLDER, "account_number": "123456",
            "routing_number": "021000021", "account_type": "current",
        })


def test_bad_ifsc():
    with pytest.raises(ValueError, match="IFSC"):
        bv.validate_bank_details("IN", {
            "account_holder_name": HOLDER, "bank_name": "b", "account_number": "50100123456", "ifsc": "BADIFSC",
        })


def test_india_requires_bank_name():
    with pytest.raises(ValueError, match="bank name"):
        bv.validate_bank_details("IN", {
            "account_holder_name": HOLDER, "account_number": "50100123456", "ifsc": "HDFC0001234",
        })


def test_short_holder_name_rejected():
    with pytest.raises(ValueError, match="full name"):
        bv.validate_bank_details("DE", {"account_holder_name": "x", "iban": "DE89370400440532013000"})


def test_bad_country():
    with pytest.raises(ValueError, match="country"):
        bv.validate_bank_details("XX!", {"account_holder_name": HOLDER, "account_number": "123456", "swift_bic": "DBSSSGSG"})
