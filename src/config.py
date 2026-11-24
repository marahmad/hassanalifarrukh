import os
import hashlib
import secrets
from pathlib import Path
from dotenv import load_dotenv

# Load .env file
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

# Directories
REELS_DIR = BASE_DIR / "instagram_archive" / "reels"
POSTS_DIR = BASE_DIR / "instagram_archive" / "posts"
AUDIO_DIR = BASE_DIR / "data" / "extracted_audio"
TRANSCRIPTS_DIR = BASE_DIR / "data" / "transcripts"
CHROMA_DB_DIR = BASE_DIR / "data" / "chroma_db"
DB_PATH = BASE_DIR / "data" / "bot_database.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Ensure data directories exist
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)

# API Keys
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "").strip()

# Business Settings (Single All-Access Monthly Plan)
FREE_TRIAL_QUESTIONS = int(os.getenv("FREE_TRIAL_QUESTIONS", "5"))
SIGNUP_REFERRAL_BONUS_QUESTIONS = int(os.getenv("SIGNUP_REFERRAL_BONUS_QUESTIONS", "3"))
MAX_REFERRAL_BONUS_QUESTIONS = int(os.getenv("MAX_REFERRAL_BONUS_QUESTIONS", "15"))
CHAT_HISTORY_CONTEXT_MESSAGES = int(os.getenv("CHAT_HISTORY_CONTEXT_MESSAGES", "20"))
CHAT_HISTORY_UI_LIMIT = int(os.getenv("CHAT_HISTORY_UI_LIMIT", "40"))
MONTHLY_PRICE_PKR = int(os.getenv("MONTHLY_SUBSCRIPTION_PKR", "1000"))
# Backwards-compatible aliases (all paid access is unified at MONTHLY_PRICE_PKR)
BASIC_PRICE_PKR = MONTHLY_PRICE_PKR
PRO_PRICE_PKR = MONTHLY_PRICE_PKR
ADMIN_PHONE = os.getenv("ADMIN_PHONE_NUMBER", "")
SUPPORT_WHATSAPP_NUMBER = os.getenv("SUPPORT_WHATSAPP_NUMBER", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_SESSION_SECRET = os.getenv("ADMIN_SESSION_SECRET", "").strip() or secrets.token_hex(32)
USER_SESSION_SECRET = os.getenv("USER_SESSION_SECRET", "").strip() or hashlib.sha256(
    f"user_session:{ADMIN_SESSION_SECRET}".encode("utf-8")
).hexdigest()
WHATSAPP_BRIDGE_SECRET = os.getenv("WHATSAPP_BRIDGE_SECRET", "").strip()

# Payment Rails (EasyPaisa, Allied Bank, Raast)
ACCOUNT_TITLE = os.getenv("ACCOUNT_TITLE", "Muhammad Abdur Rahmaan")
EASYPAISA_NUMBER = os.getenv("EASYPAISA_NUMBER", "03007560313")
ALLIED_BANK_ACCOUNT = os.getenv("ALLIED_BANK_ACCOUNT", "04630011676813700010")
RAAST_ID_NUMBER = os.getenv("RAAST_ID_NUMBER", "03007560313")

EASYPAISA = os.getenv("EASYPAISA_DETAILS", f"{EASYPAISA_NUMBER} ({ACCOUNT_TITLE})")
ALLIED_BANK = os.getenv("ALLIED_BANK_DETAILS", f"{ALLIED_BANK_ACCOUNT} ({ACCOUNT_TITLE})")
JAZZCASH = ALLIED_BANK  # Backwards compatibility
RAAST_ID = os.getenv("RAAST_ID", f"{RAAST_ID_NUMBER} ({ACCOUNT_TITLE})")
