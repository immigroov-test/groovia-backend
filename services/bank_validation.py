"""Country-aware validation + normalization of mentor bank details.

Bank identifiers differ by country, so the fields we collect and how we validate them are chosen
from the mentor's payout country:
    iban  - IBAN (SEPA + all IBAN-zone countries), ISO 13616 mod-97 checksum + per-country length
    india - local account number + IFSC (RBI branch code)
    us    - local account number + 9-digit ACH routing (ABA checksum) + account type
    uk    - local account number + 6-digit sort code
    swift - local account number + SWIFT/BIC (international wire, the fallback for everywhere else)

`validate_bank_details` returns a normalized dict split into non-secret display metadata and the
`secret` bundle (the numbers that get encrypted). It raises ValueError with a human message on the
first problem, so the API can surface it directly.
"""
import re
from typing import Any

# ISO 13616 IBAN length per country. Presence here => the "iban" scheme is used for that country.
_IBAN_LEN = {
    "AD": 24, "AE": 23, "AL": 28, "AT": 20, "AZ": 28, "BA": 20, "BE": 16, "BG": 22, "BH": 22,
    "BR": 29, "BY": 28, "CH": 21, "CR": 22, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "DO": 28,
    "EE": 20, "EG": 29, "ES": 24, "FI": 18, "FO": 18, "FR": 27, "GB": 22, "GE": 22, "GI": 23,
    "GL": 18, "GR": 27, "GT": 28, "HR": 21, "HU": 28, "IE": 22, "IL": 23, "IS": 26, "IT": 27,
    "JO": 30, "KW": 30, "KZ": 20, "LB": 28, "LC": 32, "LI": 21, "LT": 20, "LU": 20, "LV": 21,
    "MC": 27, "MD": 24, "ME": 22, "MK": 19, "MR": 27, "MT": 31, "MU": 30, "NL": 18, "NO": 15,
    "PK": 24, "PL": 28, "PS": 29, "PT": 25, "QA": 29, "RO": 24, "RS": 22, "SA": 24, "SC": 31,
    "SE": 24, "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26, "UA": 29, "VA": 22, "VG": 24,
    "XK": 20,
}
# GB is IBAN-capable, but domestic UK payouts use sort code + account number, so route GB there.
_UK = {"GB"}
_INDIA = {"IN"}
_US = {"US"}

ACCOUNT_TYPES = {"checking", "savings"}


def scheme_for_country(country_code: str | None) -> str:
    cc = (country_code or "").strip().upper()
    if cc in _INDIA:
        return "india"
    if cc in _US:
        return "us"
    if cc in _UK:
        return "uk"
    if cc in _IBAN_LEN:
        return "iban"
    return "swift"


# ── Field validators (each returns the normalized value or raises ValueError) ────────────────

def _iban(v: str) -> str:
    s = re.sub(r"\s+", "", (v or "")).upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}", s):
        raise ValueError("Enter a valid IBAN.")
    expected = _IBAN_LEN.get(s[:2])
    if expected and len(s) != expected:
        raise ValueError(f"That IBAN should be {expected} characters for {s[:2]}.")
    # mod-97 == 1 (move first 4 to the end, letters -> two-digit numbers).
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    if int(digits) % 97 != 1:
        raise ValueError("That IBAN's checksum is invalid - please re-check it.")
    return s


def _swift_bic(v: str) -> str:
    s = re.sub(r"\s+", "", (v or "")).upper()
    if not re.fullmatch(r"[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?", s):
        raise ValueError("Enter a valid SWIFT/BIC code (8 or 11 characters).")
    return s


def _us_routing(v: str) -> str:
    s = re.sub(r"\D", "", v or "")
    if len(s) != 9:
        raise ValueError("A US routing number must be 9 digits.")
    # ABA checksum.
    d = [int(c) for c in s]
    if (3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])) % 10 != 0:
        raise ValueError("That routing number's checksum is invalid - please re-check it.")
    return s


def _ifsc(v: str) -> str:
    s = re.sub(r"\s+", "", (v or "")).upper()
    if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", s):
        raise ValueError("Enter a valid IFSC code (e.g. HDFC0001234).")
    return s


def _sort_code(v: str) -> str:
    s = re.sub(r"\D", "", v or "")
    if len(s) != 6:
        raise ValueError("A UK sort code must be 6 digits.")
    return s


def _account_number(v: str, *, digits_only: bool = False, min_len: int = 4, max_len: int = 34) -> str:
    raw = (v or "").strip()
    s = re.sub(r"[\s-]", "", raw)
    if digits_only:
        if not s.isdigit():
            raise ValueError("An account number must contain only digits.")
    elif not re.fullmatch(r"[A-Za-z0-9]+", s):
        raise ValueError("An account number must be letters and digits only.")
    if not (min_len <= len(s) <= max_len):
        raise ValueError(f"An account number must be {min_len}-{max_len} characters.")
    return s


def _name(v: str) -> str:
    s = (v or "").strip()
    if len(s) < 2:
        raise ValueError("Enter the account holder's full name.")
    if len(s) > 120:
        raise ValueError("Account holder name is too long.")
    return s


def _bank_name(v: str, required: bool) -> str | None:
    s = (v or "").strip()
    if not s:
        if required:
            raise ValueError("Enter the bank name.")
        return None
    return s[:120]


def _last4(s: str) -> str:
    digits = re.sub(r"\W", "", s)
    return digits[-4:] if len(digits) >= 4 else digits


def validate_bank_details(country_code: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize a bank-details submission. Returns:
        {scheme, country_code, account_holder_name, bank_name, account_last4, secret: {...}}
    `secret` holds the values to encrypt; everything else is safe display metadata.
    Raises ValueError (human-readable) on the first invalid field.
    """
    cc = (country_code or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", cc):
        raise ValueError("Select your bank account's country.")
    scheme = scheme_for_country(cc)
    holder = _name(payload.get("account_holder_name"))

    secret: dict[str, Any] = {}
    if scheme == "iban":
        bank_name = _bank_name(payload.get("bank_name"), required=False)
        iban = _iban(payload.get("iban"))
        secret["iban"] = iban
        if payload.get("swift_bic"):
            secret["swift_bic"] = _swift_bic(payload.get("swift_bic"))
        last4 = _last4(iban)
    elif scheme == "india":
        bank_name = _bank_name(payload.get("bank_name"), required=True)
        acct = _account_number(payload.get("account_number"), digits_only=True, min_len=6, max_len=20)
        secret["account_number"] = acct
        secret["ifsc"] = _ifsc(payload.get("ifsc"))
        last4 = _last4(acct)
    elif scheme == "us":
        bank_name = _bank_name(payload.get("bank_name"), required=False)
        acct = _account_number(payload.get("account_number"), digits_only=True, min_len=4, max_len=17)
        secret["account_number"] = acct
        secret["routing_number"] = _us_routing(payload.get("routing_number"))
        atype = (payload.get("account_type") or "").strip().lower()
        if atype not in ACCOUNT_TYPES:
            raise ValueError("Choose an account type (checking or savings).")
        secret["account_type"] = atype
        last4 = _last4(acct)
    elif scheme == "uk":
        bank_name = _bank_name(payload.get("bank_name"), required=False)
        acct = _account_number(payload.get("account_number"), digits_only=True, min_len=6, max_len=10)
        secret["account_number"] = acct
        secret["sort_code"] = _sort_code(payload.get("sort_code"))
        last4 = _last4(acct)
    else:  # swift (international fallback)
        bank_name = _bank_name(payload.get("bank_name"), required=True)
        acct = _account_number(payload.get("account_number"), min_len=4, max_len=34)
        secret["account_number"] = acct
        secret["swift_bic"] = _swift_bic(payload.get("swift_bic"))
        if payload.get("bank_address"):
            secret["bank_address"] = (payload.get("bank_address") or "").strip()[:200]
        last4 = _last4(acct)

    return {
        "scheme": scheme,
        "country_code": cc,
        "account_holder_name": holder,
        "bank_name": bank_name,
        "account_last4": last4,
        "secret": secret,
    }
