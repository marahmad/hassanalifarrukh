"""
Signed HttpOnly user session cookies, OAuth CSRF state cookies,
Google ID token cryptographic verification, and WhatsApp bridge authentication.
"""

import base64
import hashlib
import hmac
import secrets
import time
from fastapi import Request, Response

from src.config import (
    GOOGLE_CLIENT_ID,
    USER_SESSION_SECRET,
    WHATSAPP_BRIDGE_SECRET,
)

USER_SESSION_COOKIE = "user_session"
OAUTH_STATE_COOKIE = "oauth_state"
GUEST_PREVIEW_COOKIE = "guest_preview_used"
MAX_USER_SESSION_AGE_SECONDS = 30 * 24 * 3600  # 30 days
MAX_OAUTH_STATE_AGE_SECONDS = 10 * 60  # 10 minutes
MAX_GUEST_PREVIEW_AGE_SECONDS = 30 * 24 * 3600  # 30 days


def is_https_request(request: Request | None) -> bool:
    """Detects whether the incoming request arrived over HTTPS (including reverse proxies)."""
    if request is None:
        return False
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(val: str) -> bytes:
    rem = len(val) % 4
    if rem:
        val += "=" * (4 - rem)
    return base64.urlsafe_b64decode(val.encode("ascii"))


def _sign_payload(payload: str) -> str:
    return hmac.new(
        USER_SESSION_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def generate_user_session_token(user_id: str) -> str:
    """Generates a tamper-proof HMAC-SHA256 signed user session token."""
    uid_b64 = _b64url_encode(str(user_id).encode("utf-8"))
    ts = str(int(time.time()))
    payload = f"{uid_b64}.{ts}"
    sig = _sign_payload(payload)
    return f"{payload}.{sig}"


def verify_user_session_token(token: str | None) -> str | None:
    """Verifies signature and expiry of a user session token; returns user_id or None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    uid_b64, ts_str, sig = parts
    try:
        created_at = int(ts_str)
        now = int(time.time())
        if now - created_at > MAX_USER_SESSION_AGE_SECONDS or created_at > now + 300:
            return None
        payload = f"{uid_b64}.{ts_str}"
        expected_sig = _sign_payload(payload)
        if not hmac.compare_digest(sig, expected_sig):
            return None
        user_id = _b64url_decode(uid_b64).decode("utf-8").strip()
        return user_id or None
    except Exception:
        return None


def get_authenticated_user_id(request: Request) -> str | None:
    """Extracts and verifies the authenticated user's internal ID from HttpOnly cookie."""
    cookie = request.cookies.get(USER_SESSION_COOKIE)
    return verify_user_session_token(cookie)


def set_user_session_cookie(response: Response, user_id: str, request: Request | None = None) -> None:
    """Sets the signed HttpOnly user_session cookie on a response."""
    token = generate_user_session_token(user_id)
    response.set_cookie(
        key=USER_SESSION_COOKIE,
        value=token,
        max_age=MAX_USER_SESSION_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https_request(request),
        path="/",
    )


def clear_user_session_cookie(response: Response) -> None:
    """Clears the user_session cookie."""
    response.delete_cookie(key=USER_SESSION_COOKIE, path="/")


def generate_guest_preview_token() -> str:
    """Generates a signed token marking that this browser has used its 1-message guest preview."""
    ts = str(int(time.time()))
    payload = f"gp.{ts}"
    sig = _sign_payload(payload)
    return f"{payload}.{sig}"


def has_used_guest_preview(request: Request) -> bool:
    """Returns True if the request carries a valid signed guest_preview_used cookie."""
    token = request.cookies.get(GUEST_PREVIEW_COOKIE)
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "gp":
        return False
    _, ts_str, sig = parts
    try:
        created_at = int(ts_str)
        now = int(time.time())
        if now - created_at > MAX_GUEST_PREVIEW_AGE_SECONDS or created_at > now + 300:
            return False
        return hmac.compare_digest(sig, _sign_payload(f"gp.{ts_str}"))
    except Exception:
        return False


def set_guest_preview_cookie(response: Response, request: Request | None = None) -> None:
    """Sets the signed HttpOnly guest_preview_used cookie after the 1 free guest reply."""
    response.set_cookie(
        key=GUEST_PREVIEW_COOKIE,
        value=generate_guest_preview_token(),
        max_age=MAX_GUEST_PREVIEW_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https_request(request),
        path="/",
    )


def generate_oauth_state() -> str:
    """Generates a cryptographically random CSRF state token for OAuth 2.0."""
    nonce = secrets.token_urlsafe(24)
    ts = str(int(time.time()))
    payload = f"oauth.{nonce}.{ts}"
    sig = _sign_payload(payload)
    return f"{payload}.{sig}"


def set_oauth_state_cookie(response: Response, state: str, request: Request | None = None) -> None:
    response.set_cookie(
        key=OAUTH_STATE_COOKIE,
        value=state,
        max_age=MAX_OAUTH_STATE_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_https_request(request),
        path="/",
    )


def verify_oauth_state(request: Request, state_param: str | None) -> bool:
    """Validates OAuth CSRF state against the HttpOnly oauth_state cookie and signature."""
    cookie_state = request.cookies.get(OAUTH_STATE_COOKIE)
    if not cookie_state or not state_param:
        return False
    if not hmac.compare_digest(cookie_state, state_param):
        return False
    parts = state_param.split(".")
    if len(parts) != 4 or parts[0] != "oauth":
        return False
    _, nonce, ts_str, sig = parts
    try:
        created_at = int(ts_str)
        now = int(time.time())
        if now - created_at > MAX_OAUTH_STATE_AGE_SECONDS or created_at > now + 60:
            return False
        expected_sig = _sign_payload(f"oauth.{nonce}.{ts_str}")
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False


def is_valid_bridge_request(request: Request) -> bool:
    """
    Verifies that an inbound request truly came from the local WhatsApp bridge
    via the shared X-Bridge-Secret header. Fails closed if secret is unset.
    """
    if not WHATSAPP_BRIDGE_SECRET:
        return False
    provided = (request.headers.get("x-bridge-secret") or "").strip()
    if not provided:
        return False
    return hmac.compare_digest(provided, WHATSAPP_BRIDGE_SECRET)


def verify_google_id_token(credential: str) -> dict | None:
    """
    Cryptographically verifies a Google ID Token (JWT) against Google's public keys,
    audience (GOOGLE_CLIENT_ID), issuer, and email_verified claim.
    """
    if not credential or not GOOGLE_CLIENT_ID:
        return None
    try:
        from google.oauth2 import id_token as google_id_token
        from google.auth.transport import requests as google_requests

        claims = google_id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
        issuer = claims.get("iss", "")
        if issuer not in ("accounts.google.com", "https://accounts.google.com"):
            return None
        if not claims.get("email") or not claims.get("email_verified", False):
            return None
        return {
            "sub": claims.get("sub", ""),
            "email": claims.get("email", "").strip().lower(),
            "name": claims.get("name") or claims.get("email", "User").split("@")[0],
            "picture": claims.get("picture", ""),
        }
    except Exception as e:
        print(f"[Auth Warning] Google ID token verification failed: {e}")
        return None
