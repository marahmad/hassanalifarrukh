import hmac
import json
import urllib.parse
from pathlib import Path

import requests
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from groq import Groq

from src.admin_auth import (
    generate_admin_session_token,
    is_admin_authenticated,
    render_admin_login_html,
)
from src.chat_frontend import render_chat_html
from src.config import (
    ACCOUNT_TITLE,
    ADMIN_PASSWORD,
    ADMIN_PHONE,
    ALLIED_BANK,
    ALLIED_BANK_ACCOUNT,
    EASYPAISA,
    EASYPAISA_NUMBER,
    FREE_TRIAL_QUESTIONS,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GROQ_API_KEY,
    JAZZCASH,
    MONTHLY_PRICE_PKR,
    RAAST_ID,
    RAAST_ID_NUMBER,
    SIGNUP_REFERRAL_BONUS_QUESTIONS,
    VERIFY_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_TOKEN,
)
from src.dashboard import render_dashboard_html
from src.database import (
    activate_user,
    apply_referral_code,
    can_user_chat,
    complete_chat_turn,
    delete_user_completely,
    evaluate_user_access,
    get_chat_conversations,
    get_or_create_google_user,
    get_or_create_user,
    get_recent_chat_history,
    get_referral_stats,
    get_subscriber_phones,
    get_system_metrics,
    get_user_by_identifier,
    get_user_trial_limit,
    init_db,
    link_phone_to_user,
    mark_welcome_sent,
    record_user_query,
    save_chat_message,
    submit_trx_id,
)
from src.knowledge_base import search_knowledge
from src.persona import build_hassan_chat_messages, build_hassan_prompt
from src.phone_utils import (
    format_pk_phone_display,
    is_canonical_pk_phone,
    is_synthetic_user_id,
    is_valid_email,
    normalize_email,
    normalize_pk_phone,
    normalize_pk_phone_or_none,
)
from src.user_auth import (
    OAUTH_STATE_COOKIE,
    clear_user_session_cookie,
    generate_oauth_state,
    get_authenticated_user_id,
    is_https_request,
    is_valid_bridge_request,
    set_oauth_state_cookie,
    set_user_session_cookie,
    verify_google_id_token,
    verify_oauth_state,
)
from src.whatsapp_client import send_whatsapp_text

app = FastAPI(title="Hassan Ali Farrukh AI Assistant")
init_db()

# Initialize Groq Client with explicit timeout so no hung upstream call can stall a worker
groq_client = Groq(api_key=GROQ_API_KEY, timeout=25.0, max_retries=1) if GROQ_API_KEY else None

PENDING_REF_COOKIE = "pending_ref"


GROQ_FALLBACK_MODELS = [
    ("openai/gpt-oss-120b", True),
    ("openai/gpt-oss-20b", True),
    ("qwen/qwen3.8-27b", False),
]


def _call_groq_model(messages: list[dict], model_id: str, max_completion: int, use_reasoning_effort: bool, temperature: float):
    kwargs = {
        "messages": messages,
        "model": model_id,
        "temperature": temperature,
        "max_completion_tokens": max_completion,
    }
    if use_reasoning_effort:
        kwargs["reasoning_effort"] = "low"
    try:
        return groq_client.chat.completions.create(**kwargs)
    except TypeError:
        return groq_client.chat.completions.create(
            messages=messages,
            model=model_id,
            temperature=temperature,
            max_tokens=max_completion,
        )


def _generate_ai_reply(messages: list[dict], status: str) -> str:
    """
    Generates a complete Hassan AI reply via Groq without mid-sentence truncation or 429 crashes.
    - Uses `reasoning_effort="low"` with balanced `max_completion_tokens` (750 trial / 1000 subscribed)
      so Groq's 8,000 TPM free-tier bucket stretches further while leaving ~700-950 visible tokens.
    - Retries with +600 tokens if `finish_reason == "length"` or output is empty.
    - Automatically fails over across Groq models (`gpt-oss-120b` -> `gpt-oss-20b` -> `qwen3.8-27b`)
      if a model hits 429 TPM rate limits during traffic spikes.
    """
    max_completion = 1000 if status == "subscribed" else 750
    last_err = None

    for model_id, supports_effort in GROQ_FALLBACK_MODELS:
        try:
            chat = _call_groq_model(messages, model_id, max_completion, supports_effort, temperature=0.75)
            choice = chat.choices[0]
            content = (choice.message.content or "").strip()
            if not content or getattr(choice, "finish_reason", None) == "length":
                try:
                    retry_chat = _call_groq_model(
                        messages,
                        model_id,
                        max_completion + 600,
                        supports_effort,
                        temperature=0.7,
                    )
                    retry_content = (retry_chat.choices[0].message.content or "").strip()
                    if retry_content:
                        content = retry_content
                except Exception:
                    pass
            if content:
                # Strip accidental **markdown bolding** if any leaked through
                return content
        except Exception as e:
            last_err = e
            print(f"[AI Failover] Model {model_id} failed ({type(e).__name__}): {e}")
            continue

    if last_err is not None:
        raise last_err
    return "Yar thoda technical glitch aa gaya tha — apna message dobara bhej dein, seedhi baat karte hain."


def _public_user_payload(user: dict) -> dict:
    contact_phone = user.get("contact_phone") or (
        user["phone_number"] if is_canonical_pk_phone(user.get("phone_number")) else ""
    )
    is_sub = bool(user.get("is_subscribed", False))
    return {
        "id": user.get("phone_number", ""),
        "name": user.get("name") or "User",
        "email": user.get("email") or "",
        "avatar_url": user.get("avatar_url") or "",
        "auth_provider": user.get("auth_provider") or "google",
        "contact_phone": contact_phone,
        "display_phone": format_pk_phone_display(contact_phone) if contact_phone else "",
        "tier": "pro" if is_sub else "trial",
        "is_subscribed": is_sub,
        "monthly_price_pkr": MONTHLY_PRICE_PKR,
        "subscription_expiry": user.get("subscription_expiry"),
        "trial_questions_used": user.get("trial_questions_used") or 0,
        "bonus_trial_questions": int(user.get("bonus_trial_questions") or 0),
        "max_free_trial": get_user_trial_limit(user),
        "referral_code": user.get("referral_code") or "",
        "referral_count": user.get("referral_count") or 0,
    }


def _is_google_oauth_enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


PAYWALL_TEXT = f"""Your free questions are up! 🎯

Unlock **Full Access — PKR {MONTHLY_PRICE_PKR:,}/month**:
• Unlimited AI mentorship chats
• Full knowledge archive & deep step-by-step answers
• Refer 1 friend → +{SIGNUP_REFERRAL_BONUS_QUESTIONS} bonus free messages instantly (3 paid friends = 1 month FREE)

━━━━━━━━━━━━━━━━
Send PKR {MONTHLY_PRICE_PKR:,} to:
• EasyPaisa: {EASYPAISA}
• Allied Bank: {ALLIED_BANK}
• Raast: {RAAST_ID}

After paying, send your screenshot on WhatsApp or reply with your Transaction ID:"""


def build_welcome_message(tier: str, referral_code: str) -> str:
    """Builds the first-time paid welcome message with referral incentives."""
    return f"""You're in! 🎉

Your Full Access pass is active — koi limit nahi, jab dil kare seedha message kar do.

━━━━━━━━━━━━━━━━
🎁 Your invite code: {referral_code}
Share it with 3 friends — jab woh subscribe karein ge, tumhe 1 month FREE mil jayega."""


def format_stats_summary(metrics: dict) -> str:
    """Formats executive analytics for WhatsApp and admin chat commands."""
    return (
        f"📊 *Hassan AI Live Business & Conversion Stats*\n\n"
        f"👥 *Audience Reach:*\n"
        f"• Total Inbound Users: {metrics['total_users']}\n"
        f"• Total Queries Handled: {metrics['total_queries']}\n\n"
        f"💳 *Subscribers & Conversion:*\n"
        f"• Active Paid Members (PKR {MONTHLY_PRICE_PKR:,}/mo): {metrics['active_subscribers']}\n"
        f"• Conversion Rate: {metrics['conversion_rate']}%\n"
        f"• Estimated MRR: PKR {metrics['estimated_mrr_pkr']:,} / month\n\n"
        f"🆓 *Trial Funnel:*\n"
        f"• Active Free Trials: {metrics['active_trials']}\n"
        f"• Paywall Reached: {metrics['paywalled_trials']}\n\n"
        f"🔗 *Viral Loop:*\n"
        f"• Total Referrals Generated: {metrics['total_referrals']}\n\n"
        f"⏳ *Pending Payments:* {metrics['pending_payments']}\n\n"
        f"🌐 Web Dashboard: https://hassanalifarrukh.store/admin/dashboard"
    )


def parse_activate_args(parts: list[str]) -> tuple[str | None, str, int, str]:
    """
    Parses `/activate <phone_or_email_or_id> [days]` (also tolerates legacy tier token).
    Accepts any Pakistani mobile format (normalized to 923XXXXXXXXX), an email address,
    or an internal user ID (u_.../google_...).
    Returns: (target_identifier_or_None, tier, days, error_message)
    """
    raw_target = parts[1].strip()
    tier = "pro"
    days = 30
    for p in parts[2:]:
        p_clean = p.strip().lower()
        if p_clean.isdigit():
            days = int(p_clean)

    if "@" in raw_target:
        if not is_valid_email(raw_target):
            return None, tier, days, "Invalid email address provided for activation."
        return normalize_email(raw_target), tier, days, ""

    if is_synthetic_user_id(raw_target):
        return raw_target, tier, days, ""

    ok, canon, err = normalize_pk_phone(raw_target)
    if not ok:
        return None, tier, days, f"Invalid target '{raw_target}'. {err} Or pass the user's email address."
    return canon, tier, days, ""


def parse_trx_text(text: str) -> tuple[str, str, str]:
    """
    Extracts TRX ID and optional friend referral code (e.g. HASSAN-XXXX).
    All subscriptions use the unified PKR 1,000/mo Full Access plan (`pro`).
    """
    cleaned = text.replace("TRX ID:", " ").replace("TRX:", " ").replace("TRX", " ")
    tokens = cleaned.split()
    trx_id = tokens[0] if tokens else ""
    plan = "pro"
    referral_code = ""

    for tok in tokens[1:]:
        t_clean = tok.strip()
        t_upper = t_clean.upper()
        if t_upper in ["BASIC", "PRO", "FULL", "MONTHLY"]:
            continue
        elif t_upper.startswith("REF:") or t_upper.startswith("CODE:"):
            referral_code = t_upper.split(":")[-1].strip()
        elif t_upper.startswith("HASSAN-"):
            referral_code = t_upper

    return trx_id, plan, referral_code


def is_trx_message(text: str, user_status: str = "free_trial") -> bool:
    """
    Checks if a message is a payment Transaction ID submission,
    either explicitly formatted (TRX: ...) or a direct raw transaction ID.
    """
    t = text.strip()
    u = t.upper()
    if u.startswith("TRX:") or u.startswith("TRX ID:") or u.startswith("TRX ") or u == "TRX":
        return True

    if user_status != "subscribed":
        tokens = t.split()
        if 1 <= len(tokens) <= 2:
            candidate = tokens[0].replace("-", "").replace("_", "")
            if 6 <= len(candidate) <= 32 and any(c.isdigit() for c in candidate) and candidate.isalnum():
                return True
    return False


# ── User Authentication Endpoints (Google Sign-In Only) ────────────────────

@app.get("/api/auth/me")
async def api_auth_me(request: Request):
    """Returns server-verified session state and Google Sign-In status."""
    auth_config = {"google_enabled": _is_google_oauth_enabled()}
    user_id = get_authenticated_user_id(request)
    if not user_id:
        return {"authenticated": False, "user": None, **auth_config}

    user = get_user_by_identifier(user_id)
    if not user:
        resp = JSONResponse({"authenticated": False, "user": None, **auth_config})
        clear_user_session_cookie(resp)
        return resp

    return {
        "authenticated": True,
        "user": _public_user_payload(user),
        **auth_config,
    }


@app.post("/api/auth/logout")
async def api_auth_logout():
    """Clears the signed user_session HttpOnly cookie."""
    resp = JSONResponse({"status": "ok", "message": "Signed out"})
    clear_user_session_cookie(resp)
    return resp


@app.get("/api/auth/google/login")
async def google_login(request: Request, ref: str = Query(None)):
    """Redirects user to Google's official OAuth 2.0 screen with CSRF state protection."""
    if not _is_google_oauth_enabled():
        return RedirectResponse(url="/?auth_error=google_not_configured")

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8080"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    redirect_uri = f"{proto}://{host}/api/auth/google/callback"
    state = generate_oauth_state()

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    resp = RedirectResponse(url=auth_url)
    set_oauth_state_cookie(resp, state, request)
    if ref and ref.strip():
        resp.set_cookie(
            key=PENDING_REF_COOKIE,
            value=ref.strip().upper()[:32],
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=is_https_request(request),
            path="/",
        )
    return resp


@app.get("/api/auth/google/callback")
async def google_callback(
    request: Request,
    code: str = Query(None),
    state: str = Query(None),
    error: str = Query(None),
):
    """Validates OAuth state, exchanges authorization code, verifies Google identity, sets HttpOnly cookie."""
    if error or not code:
        return RedirectResponse(url=f"/?auth_error={urllib.parse.quote(error or 'cancelled')}")

    if not _is_google_oauth_enabled():
        return RedirectResponse(url="/?auth_error=google_not_configured")

    if not verify_oauth_state(request, state):
        return RedirectResponse(url="/?auth_error=invalid_state")

    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8080"
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    redirect_uri = f"{proto}://{host}/api/auth/google/callback"

    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    try:
        token_resp = requests.post(token_url, data=payload, timeout=10)
        if token_resp.status_code != 200:
            return RedirectResponse(url="/?auth_error=exchange_failed")

        token_data = token_resp.json()
        id_token_str = token_data.get("id_token")
        claims = verify_google_id_token(id_token_str) if id_token_str else None

        if not claims:
            access_token = token_data.get("access_token")
            if access_token:
                u_resp = requests.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=10,
                )
                if u_resp.status_code == 200:
                    u_json = u_resp.json()
                    if u_json.get("email") and u_json.get("email_verified", False):
                        claims = {
                            "sub": u_json.get("sub", ""),
                            "email": normalize_email(u_json.get("email")),
                            "name": u_json.get("name") or u_json.get("email", "User").split("@")[0],
                            "picture": u_json.get("picture", ""),
                        }

        if not claims or not claims.get("email"):
            return RedirectResponse(url="/?auth_error=verification_failed")

        user = get_or_create_google_user(
            google_id=claims["sub"],
            email=claims["email"],
            name=claims["name"],
            avatar_url=claims["picture"],
        )

        pending_ref = (request.cookies.get(PENDING_REF_COOKIE) or "").strip().upper()
        if pending_ref:
            apply_referral_code(user["phone_number"], pending_ref)

        resp = RedirectResponse(url="/?signed_in=google")
        resp.delete_cookie(key=OAUTH_STATE_COOKIE, path="/")
        resp.delete_cookie(key=PENDING_REF_COOKIE, path="/")
        set_user_session_cookie(resp, user["phone_number"], request)
        return resp

    except Exception as e:
        print(f"Error in Google OAuth callback: {e}")
        return RedirectResponse(url="/?auth_error=exception")


@app.post("/api/auth/google")
async def api_auth_google(request: Request):
    """Authenticates user via cryptographically verified Google One-Tap ID token."""
    if not GOOGLE_CLIENT_ID:
        return JSONResponse(
            {"status": "error", "message": "Google Sign-In is not configured on the server."},
            status_code=503,
        )

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request payload."}, status_code=400)

    credential = str(data.get("credential") or "").strip()
    ref_code = str(data.get("referral_code") or "").strip().upper()
    if not credential:
        return JSONResponse(
            {"status": "error", "message": "Missing verified Google ID token credential."},
            status_code=401,
        )

    claims = verify_google_id_token(credential)
    if not claims:
        return JSONResponse(
            {"status": "error", "message": "Google credential verification failed."},
            status_code=401,
        )

    user = get_or_create_google_user(
        google_id=claims["sub"],
        email=claims["email"],
        name=claims["name"],
        avatar_url=claims["picture"],
    )
    if ref_code:
        apply_referral_code(user["phone_number"], ref_code)
        user = get_user_by_identifier(user["phone_number"]) or user

    resp = JSONResponse({"status": "ok", "user": _public_user_payload(user)})
    set_user_session_cookie(resp, user["phone_number"], request)
    return resp


@app.post("/api/auth/link-phone")
async def api_link_phone(request: Request):
    """Links and normalizes a Pakistani mobile number (923XXXXXXXXX) to the signed-in user."""
    user_id = get_authenticated_user_id(request)
    if not user_id:
        return JSONResponse(
            {"status": "error", "message": "Please sign in first to save your mobile number."},
            status_code=401,
        )

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request payload."}, status_code=400)

    raw_phone = str(data.get("phone") or "").strip()
    ok, canon, msg = link_phone_to_user(user_id, raw_phone)
    if not ok:
        status_code = 409 if "already linked" in msg.lower() else 400
        return JSONResponse({"status": "error", "message": msg}, status_code=status_code)

    updated = get_user_by_identifier(user_id)
    return {
        "status": "ok",
        "contact_phone": canon,
        "display_phone": format_pk_phone_display(canon),
        "user": _public_user_payload(updated) if updated else None,
    }


# ── Admin Endpoints ────────────────────────────────────────────────────────

@app.get("/api/admin/metrics")
async def get_metrics(request: Request):
    """Returns JSON metrics of users, revenue, trials, and conversion rate. Requires passkey."""
    if not is_admin_authenticated(request):
        return JSONResponse(
            {"status": "error", "message": "Unauthorized. Executive passkey required."},
            status_code=401,
        )
    return get_system_metrics()


@app.get("/api/admin/conversations")
async def get_admin_conversations(request: Request):
    """Returns logged user conversations from chat_history for the Executive Dashboard."""
    if not is_admin_authenticated(request):
        return JSONResponse(
            {"status": "error", "message": "Unauthorized. Executive passkey required."},
            status_code=401,
        )
    conversations = get_chat_conversations(limit_users=100, messages_per_user=100)
    return JSONResponse({"status": "ok", "conversations": conversations}, headers=NO_CACHE_HEADERS)


@app.post("/api/admin/login")
async def api_admin_login(request: Request):
    """Validates the Executive Passkey and sets a 7-day signed session cookie."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request payload"}, status_code=400)

    passkey = str(data.get("passkey") or "").strip()
    if ADMIN_PASSWORD and hmac.compare_digest(passkey, ADMIN_PASSWORD):
        token = generate_admin_session_token()
        resp = JSONResponse({"status": "ok", "message": "Access granted"})
        resp.set_cookie(
            key="admin_session",
            value=token,
            max_age=7 * 24 * 3600,
            httponly=True,
            samesite="lax",
            secure=is_https_request(request),
            path="/",
        )
        return resp
    return JSONResponse({"status": "error", "message": "Incorrect Executive Passkey"}, status_code=401)


@app.post("/api/admin/logout")
async def api_admin_logout():
    """Logs out executive admin by clearing the admin_session cookie."""
    resp = JSONResponse({"status": "ok", "message": "Logged out"})
    resp.delete_cookie(key="admin_session", path="/")
    return resp


NO_CACHE_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
}

PAGE_CACHE_HEADERS = {
    "Cache-Control": "private, no-cache, must-revalidate",
    "Vary": "Cookie",
}

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ALLOWED_STATIC_FILES = {
    "demo_preview.mp4": "video/mp4",
    "demo_poster.jpg": "image/jpeg",
}

FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<rect width="64" height="64" rx="16" fill="#0b0b0d"/>'
    '<rect x="4" y="4" width="56" height="56" rx="13" fill="#ffffff"/>'
    '<text x="32" y="42" text-anchor="middle" font-family="-apple-system,BlinkMacSystemFont,sans-serif" '
    'font-size="28" font-weight="800" fill="#0b0b0d">HF</text></svg>'
)


@app.get("/favicon.ico")
async def favicon():
    """Serves lightweight SVG favicon to eliminate iOS/Safari 404 requests."""
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


@app.get("/static/{filename}")
async def serve_static_asset(filename: str):
    """Fallback static asset handler for web demo media (served directly by Nginx in production)."""
    media_type = ALLOWED_STATIC_FILES.get(filename)
    if not media_type:
        return Response(status_code=404)
    file_path = STATIC_DIR / filename
    if not file_path.is_file():
        return Response(status_code=404)
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@app.get("/", response_class=HTMLResponse)
async def customer_chat(request: Request):
    """Customer-facing mobile chat UI — server-hydrates auth state to eliminate /api/auth/me waterfall."""
    auth_config = {"google_enabled": _is_google_oauth_enabled()}
    user_id = get_authenticated_user_id(request)
    clear_stale = False
    if user_id:
        user = get_user_by_identifier(user_id)
        if user:
            initial_auth = {"authenticated": True, "user": _public_user_payload(user), **auth_config}
        else:
            initial_auth = {"authenticated": False, "user": None, **auth_config}
            clear_stale = True
    else:
        initial_auth = {"authenticated": False, "user": None, **auth_config}

    resp = HTMLResponse(
        content=render_chat_html(initial_auth=initial_auth),
        status_code=200,
        headers=PAGE_CACHE_HEADERS,
    )
    if clear_stale:
        clear_user_session_cookie(resp)
    return resp


@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Live web analytics dashboard protected by Executive Passkey gate."""
    if not is_admin_authenticated(request):
        return HTMLResponse(content=render_admin_login_html(), status_code=200, headers=NO_CACHE_HEADERS)
    m = get_system_metrics()
    convs = get_chat_conversations(limit_users=100, messages_per_user=100)
    return HTMLResponse(content=render_dashboard_html(m, conversations=convs), status_code=200, headers=NO_CACHE_HEADERS)


@app.post("/api/admin/activate")
async def api_admin_activate(request: Request):
    """Activates or extends a user subscription by Pakistani phone (any format), email, or ID."""
    if not is_admin_authenticated(request):
        return JSONResponse(
            {"status": "error", "message": "Unauthorized. Executive passkey required."},
            status_code=401,
        )
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request payload."}, status_code=400)

    raw_target = str(data.get("identifier") or data.get("phone") or "").strip()
    days_raw = data.get("days", 30)
    try:
        days = max(1, int(days_raw))
    except Exception:
        days = 30

    if not raw_target:
        return JSONResponse({"status": "error", "message": "Please enter a phone number or email."}, status_code=400)

    target_id, tier_parsed, days_parsed, err = parse_activate_args(["/activate", raw_target, str(days)])
    if not target_id:
        return JSONResponse({"status": "error", "message": err}, status_code=400)

    new_exp, tier_activated, ref_code, referrer_reward = activate_user(target_id, days=days_parsed, tier=tier_parsed)
    return {
        "status": "ok",
        "target": target_id,
        "tier": tier_activated,
        "expiry": new_exp.strftime("%Y-%m-%d"),
        "referral_code": ref_code,
        "referrer_reward": {
            "referrer_phone": referrer_reward["referrer_phone"],
            "new_expiry": referrer_reward["new_expiry"].strftime("%Y-%m-%d"),
        } if referrer_reward else None,
    }


@app.post("/api/admin/delete-user")
async def api_admin_delete_user(request: Request):
    """Permanently deletes a user and all associated chats, payments, and leads. Requires passkey."""
    if not is_admin_authenticated(request):
        return JSONResponse(
            {"status": "error", "message": "Unauthorized. Executive passkey required."},
            status_code=401,
        )
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid request payload."}, status_code=400)

    raw_target = str(data.get("identifier") or data.get("user_id") or data.get("phone") or "").strip()
    if not raw_target:
        return JSONResponse(
            {"status": "error", "message": "Please specify a user phone number, email, or ID to delete."},
            status_code=400,
        )

    ok, result = delete_user_completely(raw_target)
    if not ok:
        return JSONResponse({"status": "error", "message": str(result)}, status_code=404)

    return JSONResponse({"status": "ok", "deleted": result}, headers=NO_CACHE_HEADERS)


@app.post("/api/admin/broadcast")
async def api_admin_broadcast(request: Request):
    """Dispatches a broadcast message to all active subscribers. Requires passkey."""
    if not is_admin_authenticated(request):
        return JSONResponse(
            {"status": "error", "message": "Unauthorized. Executive passkey required."},
            status_code=401,
        )
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return JSONResponse(
            {
                "status": "error",
                "message": "WhatsApp Cloud API is not configured yet (set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env). No messages were sent.",
            },
            status_code=503,
        )

    data = await request.json()
    msg = str(data.get("message") or "").strip()
    if not msg:
        return JSONResponse({"status": "error", "message": "Empty message"}, status_code=400)

    phones = get_subscriber_phones("all")
    formatted_msg = f"[Hassan AI] 💬 Update:\n\n\"{msg}\"\n\n— Hassan AI"
    sent = 0
    for p in phones:
        try:
            if send_whatsapp_text(p, formatted_msg):
                sent += 1
        except Exception:
            pass

    return {"status": "ok", "count": sent, "total_targeted": len(phones), "tier": "all"}


# ── Meta WhatsApp Cloud API Webhook ────────────────────────────────────────

@app.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta Webhook Verification endpoint."""
    if VERIFY_TOKEN and hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(content="Verification failed", status_code=403)


@app.post("/webhook")
async def receive_whatsapp(request: Request):
    """Handles incoming WhatsApp messages from Meta Cloud API."""
    data = await request.json()

    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ok"}

        message = messages[0]
        raw_sender = str(message.get("from") or "").strip()
        sender_phone = normalize_pk_phone_or_none(raw_sender) or raw_sender.replace("+", "").strip()
        msg_type = message.get("type")

        if msg_type != "text":
            send_whatsapp_text(sender_phone, "Please send a text message. Voice notes and media messages are not supported yet.")
            return {"status": "ok"}

        user_text = message.get("text", {}).get("body", "").strip()
        print(f"Incoming from +{sender_phone}: {user_text}")

        norm_admin_phone = normalize_pk_phone_or_none(ADMIN_PHONE) if ADMIN_PHONE else None
        is_wa_admin = bool(norm_admin_phone and sender_phone == norm_admin_phone)

        # 1. Admin Commands Handler (/activate, /stats, /broadcast) — STRICT admin check
        is_admin_cmd = (
            user_text.startswith("/activate")
            or user_text in ["/stats", "/metrics"]
            or user_text.startswith("/broadcast")
        )
        if is_admin_cmd:
            if not is_wa_admin:
                send_whatsapp_text(sender_phone, "⛔ Unauthorized: Admin access required to run executive commands.")
                return {"status": "ok"}

            if user_text in ["/stats", "/metrics"]:
                stats_msg = format_stats_summary(get_system_metrics())
                send_whatsapp_text(sender_phone, stats_msg)
                return {"status": "ok"}

            if user_text.startswith("/broadcast"):
                msg_body = ""
                for prefix in ("/broadcast-basic ", "/broadcast-pro ", "/broadcast "):
                    if user_text.startswith(prefix):
                        msg_body = user_text[len(prefix):].strip()
                        break

                if msg_body:
                    phones = get_subscriber_phones("all")
                    formatted_msg = f"[Hassan AI] 💬 Update:\n\n\"{msg_body}\"\n\n— Hassan AI"
                    for p in phones:
                        try:
                            send_whatsapp_text(p, formatted_msg)
                        except Exception:
                            pass
                    send_whatsapp_text(sender_phone, f"📢 Broadcast successfully delivered to {len(phones)} active subscribers!")
                    return {"status": "ok"}

            if user_text.startswith("/activate"):
                parts = user_text.split()
                if len(parts) >= 2:
                    target_id, tier, days, err = parse_activate_args(parts)
                    if not target_id:
                        send_whatsapp_text(sender_phone, f"❌ {err}")
                        return {"status": "ok"}

                    new_exp, tier_activated, ref_code, referrer_reward = activate_user(target_id, days=days, tier=tier)
                    activated_user = get_user_by_identifier(target_id) or {}
                    target_wa = activated_user.get("contact_phone") or (
                        target_id if is_canonical_pk_phone(target_id) else ""
                    )

                    label = f"+{target_wa}" if target_wa else target_id
                    send_whatsapp_text(
                        sender_phone,
                        f"✅ User {label} activated on Full Access (PKR {MONTHLY_PRICE_PKR:,}/mo) for {days} days until {new_exp.strftime('%Y-%m-%d')}!\nReferral Code: {ref_code}",
                    )

                    if target_wa:
                        welcome_msg = build_welcome_message(tier_activated, ref_code)
                        send_whatsapp_text(target_wa, welcome_msg)
                        mark_welcome_sent(target_id)

                    if referrer_reward and is_canonical_pk_phone(referrer_reward.get("referrer_phone")):
                        ref_phone = referrer_reward["referrer_phone"]
                        ref_exp_fmt = referrer_reward["new_expiry"].strftime("%Y-%m-%d")
                        ref_reward_msg = (
                            f"🎉 Congratulations!\n\n"
                            f"3 friends subscribed using your code.\n"
                            f"Your account has been extended by 30 days — FREE.\n\n"
                            f"Current expiry: {ref_exp_fmt}\n\n"
                            f"Thank you for spreading the word! 🙌"
                        )
                        send_whatsapp_text(ref_phone, ref_reward_msg)

                    return {"status": "ok"}

        # 2. Check for Referral Stats Command (REFERRAL / CODE)
        if user_text.upper() in ["REFERRAL", "CODE", "MY CODE", "REF"]:
            stats = get_referral_stats(sender_phone)
            reply = (
                f"🎁 *Your Hassan AI Referral Hub*\n\n"
                f"Your Referral Code: `{stats['code']}`\n"
                f"Friends Subscribed: {stats['count']}\n"
                f"Remaining for Next Free Month: {stats['needed']}\n\n"
                f"Share your code with friends. When 3 friends subscribe with it, your account automatically gets 30 days added for FREE!"
            )
            send_whatsapp_text(sender_phone, reply)
            return {"status": "ok"}

        # 3. Check for standalone Referral Code application (e.g. REF: HASSAN-XXXX)
        if user_text.upper().startswith("REF:") or user_text.upper().startswith("CODE:") or user_text.upper().startswith("REFERRAL:"):
            code_input = user_text.split(":")[-1].strip()
            ok, ref_msg = apply_referral_code(sender_phone, code_input)
            send_whatsapp_text(sender_phone, ref_msg)
            return {"status": "ok"}

        # 4. Check for Payment Submission
        can_chat, status, tier = can_user_chat(sender_phone)
        if is_trx_message(user_text, status):
            trx_clean, plan_chosen, ref_code_in_trx = parse_trx_text(user_text)
            if not trx_clean:
                send_whatsapp_text(sender_phone, "Please provide a valid Transaction ID.")
                return {"status": "ok"}

            ref_note = ""
            if ref_code_in_trx:
                ok_ref, ref_msg = apply_referral_code(sender_phone, ref_code_in_trx)
                if ok_ref:
                    ref_note = f"\n(Referral code {ref_code_in_trx} recorded!)"

            saved = submit_trx_id(sender_phone, trx_clean, plan_selected=plan_chosen)
            if saved:
                ack_msg = (
                    f"Thank you! Your payment verification request for Full Access (PKR {MONTHLY_PRICE_PKR:,}/mo) has been received.{ref_note}\n"
                    "Admin will verify and activate your pass within 10-15 minutes."
                )
                send_whatsapp_text(sender_phone, ack_msg)
                if norm_admin_phone:
                    admin_alert = (
                        f"🔔 *New Payment Alert!*\n"
                        f"User: +{sender_phone}\n"
                        f"Plan: Full Access (PKR {MONTHLY_PRICE_PKR:,}/mo)\n"
                        f"TRX: `{trx_clean}`\n\n"
                        f"To activate, reply with:\n"
                        f"`/activate {sender_phone} 30`"
                    )
                    send_whatsapp_text(norm_admin_phone, admin_alert)
            else:
                send_whatsapp_text(sender_phone, "This Transaction ID has already been registered or submitted.")
            return {"status": "ok"}

        # 5. Check Subscription & Trial Status
        can_chat, status, tier = can_user_chat(sender_phone)
        if not can_chat:
            send_whatsapp_text(sender_phone, PAYWALL_TEXT)
            return {"status": "ok"}

        # 6. Search Knowledge Base (Full 6-chunk archive for all paid subscribers)
        retrieval_count = 6 if status == "subscribed" else 3
        matching_chunks = search_knowledge(user_text, n_results=retrieval_count)

        # 7. Retrieve Conversation History
        history = get_recent_chat_history(sender_phone, limit=6)

        # 8. Generate AI Response via Groq
        u_pre = get_or_create_user(sender_phone)
        if groq_client:
            messages = build_hassan_chat_messages(
                user_text,
                matching_chunks,
                tier=tier,
                history=history,
                user_name=u_pre.get("name", ""),
            )
            ai_reply = _generate_ai_reply(messages, status)
        else:
            ai_reply = f"[Demo Mode] Hassan AI: I received your question: '{user_text}'."

        save_chat_message(sender_phone, "user", user_text)
        save_chat_message(sender_phone, "assistant", ai_reply)
        record_user_query(sender_phone)
        u_info = get_or_create_user(sender_phone)

        if status == "free_trial":
            remaining = get_user_trial_limit(u_info) - (u_info.get("trial_questions_used") or 0)
            if remaining > 0:
                ai_reply += f"\n\n_(Free Trial: {remaining} question{'s' if remaining != 1 else ''} remaining)_"
            else:
                ai_reply += f"\n\n_(You have used your last free question. Subscribe for PKR {MONTHLY_PRICE_PKR:,}/month for unlimited access, or invite a friend for +{SIGNUP_REFERRAL_BONUS_QUESTIONS} bonus free messages.)_"

        elif status == "subscribed" and u_info.get("total_queries", 0) > 0 and u_info.get("total_queries", 0) % 10 == 0:
            stats = get_referral_stats(sender_phone)
            reminder = (
                f"\n\n━━━━━━━━━━━━━━━━\n"
                f"Quick reminder 👋\n"
                f"Your referral code is: `{stats['code']}`\n"
                f"You've referred {stats['count']} friend{'s' if stats['count'] != 1 else ''} so far.\n"
                f"Share it and get 1 month FREE when you hit 3!"
            )
            ai_reply += reminder

        send_whatsapp_text(sender_phone, ai_reply)

    except Exception as e:
        print(f"Error processing webhook: {e}")

    return {"status": "ok"}


# ── Authenticated Chat Endpoint (Web Session Cookie / Secret Bridge / Admin) ─

@app.post("/api/chat")
async def process_chat(request: Request):
    """
    Authenticated chat endpoint:
      - Web users: identified STRICTLY via signed HttpOnly `user_session` cookie (never body `sender`).
      - WhatsApp QR bridge: authenticated via `X-Bridge-Secret` header + canonical PK phone normalization.
      - Admin dashboard commands: authenticated via signed HttpOnly `admin_session` cookie.
    """
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "reply": "Invalid request payload."}, status_code=400)

    user_text = str(data.get("message") or "").strip()
    if not user_text:
        return {"reply": "Please send a text message with your question."}

    is_bridge = is_valid_bridge_request(request)
    is_web_admin = is_admin_authenticated(request)
    session_user_id = get_authenticated_user_id(request)
    norm_admin_phone = normalize_pk_phone_or_none(ADMIN_PHONE) if ADMIN_PHONE else None

    # 1. Admin Commands Handler (/activate, /stats, /broadcast)
    is_admin_cmd = (
        user_text.startswith("/activate")
        or user_text in ["/stats", "/metrics"]
        or user_text.startswith("/broadcast")
    )
    if is_admin_cmd:
        bridge_sender = normalize_pk_phone_or_none(data.get("sender")) if is_bridge else None
        is_bridge_admin = bool(is_bridge and norm_admin_phone and bridge_sender == norm_admin_phone)
        if not (is_web_admin or is_bridge_admin):
            return JSONResponse(
                {"status": "error", "reply": "⛔ Unauthorized: Admin authentication required to run executive commands."},
                status_code=403,
            )

        if user_text in ["/stats", "/metrics"]:
            return {"reply": format_stats_summary(get_system_metrics())}

        if user_text.startswith("/broadcast"):
            msg_body = ""
            for prefix in ("/broadcast-basic ", "/broadcast-pro ", "/broadcast "):
                if user_text.startswith(prefix):
                    msg_body = user_text[len(prefix):].strip()
                    break

            if msg_body:
                phones = get_subscriber_phones("all")
                formatted_msg = f"[Hassan AI] 💬 Update:\n\n\"{msg_body}\"\n\n— Hassan AI"
                for p in phones:
                    try:
                        send_whatsapp_text(p, formatted_msg)
                    except Exception:
                        pass
                return {"reply": f"📢 Broadcast successfully delivered to {len(phones)} active subscribers!"}

        if user_text.startswith("/activate"):
            parts = user_text.split()
            if len(parts) < 2:
                return JSONResponse(
                    {"status": "error", "reply": "Usage: /activate <phone_or_email> [days]"},
                    status_code=400,
                )
            target_id, tier, days, err = parse_activate_args(parts)
            if not target_id:
                return JSONResponse({"status": "error", "reply": err}, status_code=400)

            new_exp, tier_activated, ref_code, referrer_reward = activate_user(target_id, days=days, tier=tier)
            label = f"+{target_id}" if is_canonical_pk_phone(target_id) else target_id
            reply = (
                f"✅ User {label} activated on Full Access (PKR {MONTHLY_PRICE_PKR:,}/mo) for {days} days until {new_exp.strftime('%Y-%m-%d')}!\n"
                f"Referral Code: {ref_code}"
            )
            if referrer_reward:
                ref_label = (
                    f"+{referrer_reward['referrer_phone']}"
                    if is_canonical_pk_phone(referrer_reward["referrer_phone"])
                    else referrer_reward["referrer_phone"]
                )
                reply += f"\n🎉 Referrer {ref_label} unlocked 30 FREE DAYS! New expiry: {referrer_reward['new_expiry'].strftime('%Y-%m-%d')}"
            return {"status": "ok", "reply": reply}

    # 2. Authenticate Caller Identity (NEVER trust raw `sender` from unauthenticated browsers)
    current_user = None
    if is_bridge:
        ok_p, canon_sender, err_p = normalize_pk_phone(data.get("sender"))
        if not ok_p:
            return JSONResponse({"status": "error", "reply": err_p}, status_code=400)
        sender_id = canon_sender
        current_user = get_or_create_user(sender_id)
        sender_id = current_user["phone_number"]
    elif session_user_id:
        existing = get_user_by_identifier(session_user_id)
        if not existing:
            resp = JSONResponse(
                {
                    "status": "error",
                    "code": "unauthenticated",
                    "reply": "Your session has expired. Please sign in again.",
                },
                status_code=401,
            )
            clear_user_session_cookie(resp)
            return resp
        current_user = existing
        sender_id = existing["phone_number"]
    else:
        return JSONResponse(
            {
                "status": "error",
                "code": "unauthenticated",
                "reply": "Please sign in with Google to start chatting.",
            },
            status_code=401,
        )

    # Optional atomic phone link if supplied alongside TRX from web UI
    supplied_phone = str(data.get("phone") or "").strip()
    if supplied_phone and not is_bridge:
        ok_link, canon_linked, link_err = link_phone_to_user(sender_id, supplied_phone)
        if not ok_link:
            status_code = 409 if "already linked" in link_err.lower() else 400
            return JSONResponse({"status": "error", "reply": link_err}, status_code=status_code)
        current_user["contact_phone"] = canon_linked

    # 3. Check for Referral Stats Command (REFERRAL / CODE)
    if user_text.upper() in ["REFERRAL", "CODE", "MY CODE", "REF"]:
        stats = get_referral_stats(sender_id)
        return {
            "reply": (
                f"🎁 *Your Hassan AI Referral Hub*\n\n"
                f"Your Referral Code: `{stats['code']}`\n"
                f"Bonus Free Messages Earned: +{stats['bonus_questions']} (up to +{stats['max_bonus_questions']})\n"
                f"Friends Subscribed: {stats['count']}\n"
                f"Remaining for Next Free Month: {stats['needed']}\n\n"
                f"• 1 friend signs up → you get +{SIGNUP_REFERRAL_BONUS_QUESTIONS} bonus free messages instantly\n"
                f"• 3 friends subscribe → you automatically get 1 Month FREE!"
            )
        }

    # 4. Check for standalone Referral Code application (e.g. REF: HASSAN-XXXX)
    if user_text.upper().startswith("REF:") or user_text.upper().startswith("CODE:") or user_text.upper().startswith("REFERRAL:"):
        code_input = user_text.split(":")[-1].strip()
        ok, ref_msg = apply_referral_code(sender_id, code_input)
        return {"reply": ref_msg}

    # Evaluate access in-memory from already-fetched user row (zero redundant DB round trip)
    can_chat, status, tier = evaluate_user_access(current_user)

    # 5. Check for Payment Submission
    if is_trx_message(user_text, status):
        trx_clean, plan_chosen, ref_code_in_trx = parse_trx_text(user_text)
        if not trx_clean:
            return {"reply": "Please provide a valid Transaction ID."}

        u_curr = current_user
        if not is_bridge and not u_curr.get("contact_phone"):
            return JSONResponse(
                {
                    "status": "error",
                    "code": "phone_required",
                    "reply": "Please enter your Pakistani mobile / EasyPaisa number so we can verify your payment.",
                },
                status_code=400,
            )

        ref_note = ""
        if ref_code_in_trx:
            ok_ref, ref_msg = apply_referral_code(sender_id, ref_code_in_trx)
            if ok_ref:
                ref_note = f"\n(Referral code {ref_code_in_trx} recorded!)"

        saved = submit_trx_id(sender_id, trx_clean, plan_selected=plan_chosen)
        if saved:
            return {
                "reply": (
                    f"✅ Thank you! Your payment verification request for Full Access (PKR {MONTHLY_PRICE_PKR:,}/mo) has been received (TRX: {trx_clean}).{ref_note}\n\n"
                    "Admin will verify and activate your pass within 10-15 minutes."
                ),
                "user": _public_user_payload(u_curr),
            }
        return {"reply": "This Transaction ID has already been registered or submitted for review."}

    # 6. Check Subscription & Trial Status
    if not can_chat:
        return {"reply": PAYWALL_TEXT, "paywalled": True, "user": _public_user_payload(current_user)}

    # 7. Search Knowledge Base (Full 6-chunk archive for all paid subscribers)
    retrieval_count = 6 if status == "subscribed" else 3
    matching_chunks = search_knowledge(user_text, n_results=retrieval_count)

    # 8. Retrieve Conversation History for Multi-Turn Context
    history = get_recent_chat_history(sender_id, limit=6)

    # 9. Generate AI Response (never deduct a trial question if upstream LLM fails)
    if groq_client:
        messages = build_hassan_chat_messages(
            user_text,
            matching_chunks,
            tier=tier,
            history=history,
            user_name=current_user.get("name", ""),
        )
        try:
            ai_reply = _generate_ai_reply(messages, status)
        except Exception as e:
            print(f"[AI Error] Groq generation failed for {sender_id}: {e}")
            return JSONResponse(
                {
                    "status": "error",
                    "reply": "Server pe thoda load zyada aa gaya tha — apna message kuch seconds baad dobara bhej dein. Aapka free question count nahi hua.",
                    "user": _public_user_payload(current_user),
                },
                status_code=503,
            )
    else:
        ai_reply = f"[Demo Mode] Hassan AI: I received your question: '{user_text}'."

    # Single pooled DB round trip: save user + assistant messages, increment usage, return updated user
    u_info = complete_chat_turn(sender_id, user_text, ai_reply)

    welcome_prefix = ""
    if status == "subscribed" and not u_info.get("welcome_sent"):
        welcome_prefix = build_welcome_message(tier, u_info.get("referral_code", "")) + "\n\n━━━━━━━━━━━━━━━━\n\n"
        mark_welcome_sent(sender_id)

    if status == "free_trial":
        remaining = get_user_trial_limit(u_info) - (u_info.get("trial_questions_used") or 0)
        if remaining > 0:
            ai_reply += f"\n\n_(Free Trial: {remaining} question{'s' if remaining != 1 else ''} remaining)_"
        else:
            ai_reply += f"\n\n_(You have used your last free question. Subscribe for PKR {MONTHLY_PRICE_PKR:,}/month for unlimited access, or invite a friend for +{SIGNUP_REFERRAL_BONUS_QUESTIONS} bonus free messages.)_"

    elif status == "subscribed" and u_info.get("total_queries", 0) > 0 and u_info.get("total_queries", 0) % 10 == 0:
        stats = get_referral_stats(sender_id)
        reminder = (
            f"\n\n━━━━━━━━━━━━━━━━\n"
            f"Quick reminder 👋\n"
            f"Your referral code is: `{stats['code']}`\n"
            f"You've referred {stats['count']} friend{'s' if stats['count'] != 1 else ''} so far.\n"
            f"Share it and get 1 month FREE when you hit 3!"
        )
        ai_reply += reminder

    return {
        "reply": welcome_prefix + ai_reply,
        "user": _public_user_payload(u_info),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.server:app", host="0.0.0.0", port=8080, reload=True)
