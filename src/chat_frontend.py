"""
Customer-facing chat frontend for Hassan Ali Farrukh AI.
Includes 3-Bar Hamburger Drawer for access plans, profile, and knowledge stats.
Features Google 1-Tap & OAuth Authentication, Cursor-inspired dark theme,
mobile-native touch controls, interactive paywall, and real-time Groq/ChromaDB chat.
"""

from pathlib import Path
from src.config import (
    BASIC_PRICE_PKR, PRO_PRICE_PKR, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
    EASYPAISA, ALLIED_BANK, JAZZCASH, RAAST_ID, FREE_TRIAL_QUESTIONS,
    ACCOUNT_TITLE, EASYPAISA_NUMBER, ALLIED_BANK_ACCOUNT, RAAST_ID_NUMBER
)

TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "chat.html"

def render_chat_html() -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    google_oauth_enabled = "true" if (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET) else "false"
    return (
        template
        .replace("__BASIC_PRICE__", str(BASIC_PRICE_PKR))
        .replace("__PRO_PRICE__", str(PRO_PRICE_PKR))
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
