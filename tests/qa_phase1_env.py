import os
from pathlib import Path
from dotenv import load_dotenv

here = Path(__file__).resolve().parent
candidates = [here, here.parent, here / ".." / ".."]
loaded = False
for c in candidates:
    env_file = c / ".env"
    if env_file.exists():
        load_dotenv(str(env_file))
        print(f"Loaded .env from: {env_file}")
        loaded = True
        break

if not loaded:
    print("WARNING: No .env file found in candidates!")

print("=== PHASE 1: ENVIRONMENT & SECRETS QA ===")

tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
print(f"TELEGRAM_BOT_TOKEN: {'PRESENT (' + tg_token[:8] + '...)' if tg_token else 'MISSING'}")
print(f"TELEGRAM_CHAT_ID:   {'PRESENT (' + tg_chat + ')' if tg_chat else 'MISSING'}")

groq_key = os.environ.get("GROQ_API_KEY", "").strip()
print(f"GROQ_API_KEY:       {'PRESENT (' + groq_key[:8] + '...)' if groq_key else 'MISSING'}")

sb_url = os.environ.get("SUPABASE_URL", "").strip()
sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or os.environ.get("SUPABASE_ANON_KEY", "").strip()
print(f"SUPABASE_URL:       {'PRESENT (' + sb_url[:20] + '...)' if sb_url else 'MISSING'}")
print(f"SUPABASE_KEY:       {'PRESENT' if sb_key else 'MISSING'}")

# Check cookies in candidates
for c in candidates:
    cf = c / "cookies_tiktok.txt"
    if cf.exists():
        print(f"cookies_tiktok.txt: PRESENT ({cf.stat().st_size} bytes at {cf})")
        break
else:
    print("cookies_tiktok.txt: MISSING")

print("=== PHASE 1 COMPLETE ===")
