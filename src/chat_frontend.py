"""
Customer-facing chat frontend for Hassan Ali Farrukh AI.
Includes 3-Bar Hamburger Drawer for access plans, profile, and knowledge stats.
Features Google 1-Tap & OAuth Authentication, Cursor-inspired dark theme,
mobile-native touch controls, interactive paywall, v4 video demo, and real-time Groq/ChromaDB chat.
"""

import json
from pathlib import Path
from src.config import (
    MONTHLY_PRICE_PKR, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    EASYPAISA, ALLIED_BANK, JAZZCASH, RAAST_ID, FREE_TRIAL_QUESTIONS,
    ACCOUNT_TITLE, EASYPAISA_NUMBER, ALLIED_BANK_ACCOUNT, RAAST_ID_NUMBER
)

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "chat.html"

_TEMPLATE_CACHE_MTIME: float = 0.0
_TEMPLATE_BASE_HTML: str = ""


def _get_base_template() -> str:
    """Caches chat.html with static config placeholders resolved in memory (invalidates on mtime change)."""
    global _TEMPLATE_CACHE_MTIME, _TEMPLATE_BASE_HTML
    mtime = TEMPLATE_PATH.stat().st_mtime
    if _TEMPLATE_BASE_HTML and mtime == _TEMPLATE_CACHE_MTIME:
        return _TEMPLATE_BASE_HTML

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    google_oauth_enabled = "true" if (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) else "false"
    formatted_price = f"{MONTHLY_PRICE_PKR:,}"
    _TEMPLATE_BASE_HTML = (
        template
        .replace("__MONTHLY_PRICE__", formatted_price)
        .replace("__MONTHLY_PRICE_RAW__", str(MONTHLY_PRICE_PKR))
        .replace("__ACCOUNT_TITLE__", str(ACCOUNT_TITLE))
        .replace("__EASYPAISA_NUMBER__", str(EASYPAISA_NUMBER))
        .replace("__ALLIED_BANK_ACCOUNT__", str(ALLIED_BANK_ACCOUNT))
        .replace("__RAAST_ID_NUMBER__", str(RAAST_ID_NUMBER))
        .replace("__EASYPAISA__", str(EASYPAISA))
        .replace("__ALLIED_BANK__", str(ALLIED_BANK))
        .replace("__JAZZCASH__", str(JAZZCASH))
        .replace("__RAAST_ID__", str(RAAST_ID))
        .replace("__FREE_TRIAL_QUESTIONS__", str(FREE_TRIAL_QUESTIONS))
        .replace("__GOOGLE_CLIENT_ID__", str(GOOGLE_CLIENT_ID))
        .replace("__GOOGLE_OAUTH_ENABLED__", google_oauth_enabled)
    )
    _TEMPLATE_CACHE_MTIME = mtime
    return _TEMPLATE_BASE_HTML


def render_chat_html(initial_auth: dict | None = None) -> str:
    """Renders the chat UI with optional server-side hydrated auth state (eliminates /api/auth/me waterfall)."""
    base = _get_base_template()
    if initial_auth is None:
        initial_auth = {
            "authenticated": False,
            "user": None,
            "google_enabled": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET),
        }
    auth_json = json.dumps(initial_auth, separators=(",", ":")).replace("</", "<\\/")
    onboarding_cls = "" if initial_auth.get("authenticated") else " active"
    return (
        base
        .replace("__INITIAL_AUTH_JSON__", auth_json)
        .replace("__ONBOARDING_MODAL_CLASS__", onboarding_cls)
    )
