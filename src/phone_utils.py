"""
Canonical Pakistani mobile number normalization & validation utilities.

All valid Pakistani mobile numbers are normalized to 12-digit E.164 format
without a leading '+':
    923XXXXXXXXX   (e.g. 923007560313)

Supported inputs that all map to 923007560313:
    03007560313
    +923007560313
    923007560313
    +9203007560313
    9203007560313
    00923007560313
    009203007560313
    3007560313
    +92 300-756 0313
    (0300) 756-0313
"""

import re

_PK_CANONICAL_RE = re.compile(r"^923\d{9}$")
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")
_INVISIBLE_CHARS_RE = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff\u00a0]")
_ALLOWED_PHONE_CHARS_RE = re.compile(r"^[\d\s\+\-\(\)\.]+$")

INVALID_PK_PHONE_MSG = (
    "Please enter a valid Pakistani mobile number "
    "(e.g. 0300 7560313 or +92 300 7560313)."
)


def is_synthetic_user_id(value: str | None) -> bool:
    """Returns True for internal non-phone user IDs (e.g. u_..., google_..., g_..., guest)."""
    if not value:
        return False
    v = str(value).strip().lower()
    return (
        v == "guest"
        or v.startswith("u_")
        or v.startswith("google_")
        or v.startswith("g_")
        or v.startswith("email_")
        or "@" in v
    )


def is_canonical_pk_phone(value: str | None) -> bool:
    """Returns True if value is already in canonical 923XXXXXXXXX format."""
    if not value:
        return False
    return bool(_PK_CANONICAL_RE.fullmatch(str(value).strip()))


def normalize_pk_phone(raw: str | None) -> tuple[bool, str, str]:
    """
    Normalizes any Pakistani mobile phone input into canonical '923XXXXXXXXX'.

    Returns:
        (is_valid, canonical_phone, error_message)
    """
    if raw is None:
        return False, "", INVALID_PK_PHONE_MSG

    cleaned = _INVISIBLE_CHARS_RE.sub("", str(raw)).strip()
    if not cleaned:
        return False, "", INVALID_PK_PHONE_MSG

    if "@" in cleaned or is_synthetic_user_id(cleaned):
        return False, "", INVALID_PK_PHONE_MSG

    # Reject inputs containing letters or unexpected symbols
    if not _ALLOWED_PHONE_CHARS_RE.fullmatch(cleaned):
        return False, "", INVALID_PK_PHONE_MSG

    # '+' is only valid as a leading prefix (allow whitespace before it, already stripped)
    if "+" in cleaned[1:]:
        return False, "", INVALID_PK_PHONE_MSG

    digits = re.sub(r"\D", "", cleaned)

    canonical = ""
    if digits.startswith("00920") and len(digits) == 15:
        # 009203007560313 -> 923007560313
        canonical = "92" + digits[5:]
    elif digits.startswith("0092") and len(digits) == 14:
        # 00923007560313 -> 923007560313
        canonical = digits[2:]
    elif digits.startswith("920") and len(digits) == 13:
        # +9203007560313 / 9203007560313 -> 923007560313
        canonical = "92" + digits[3:]
    elif digits.startswith("92") and len(digits) == 12:
        # +923007560313 / 923007560313 -> 923007560313
        canonical = digits
    elif digits.startswith("03") and len(digits) == 11:
        # 03007560313 -> 923007560313
        canonical = "92" + digits[1:]
    elif digits.startswith("3") and len(digits) == 10:
        # 3007560313 -> 923007560313
        canonical = "92" + digits
    else:
        return False, "", INVALID_PK_PHONE_MSG

    if not _PK_CANONICAL_RE.fullmatch(canonical):
        return False, "", INVALID_PK_PHONE_MSG

    return True, canonical, ""


def normalize_pk_phone_or_none(raw: str | None) -> str | None:
    """Convenience helper returning canonical 923XXXXXXXXX or None if invalid."""
    ok, canonical, _ = normalize_pk_phone(raw)
    return canonical if ok else None


def format_pk_phone_display(phone: str | None) -> str:
    """Formats a Pakistani mobile number cleanly for UI display: '+92 300 7560313'."""
    if not phone:
        return ""
    canon = normalize_pk_phone_or_none(phone)
    if canon:
        return f"+92 {canon[2:5]} {canon[5:]}"
    return str(phone).strip()


def normalize_email(raw: str | None) -> str:
    """Normalizes an email address (trimmed, lowercased)."""
    if not raw:
        return ""
    return _INVISIBLE_CHARS_RE.sub("", str(raw)).strip().lower()


def is_valid_email(raw: str | None) -> bool:
    """Validates basic email format."""
    email = normalize_email(raw)
    if not email or len(email) > 254:
        return False
    return bool(_EMAIL_RE.fullmatch(email))
