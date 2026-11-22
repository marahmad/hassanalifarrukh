"""
Idempotent database migration & phone deduplication script.
Normalizes all Pakistani mobile numbers across `users`, `payment_requests`,
`coaching_leads`, and `chat_history` to canonical '923XXXXXXXXX' format.

Usage:
    python tools/migrate_phone_numbers.py
"""
import json
import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

import src.database as database


def main():
    print("=== Running Phone Normalization & Deduplication Migration ===")

    # 1. Run against primary configured database (Supabase PostgreSQL if DATABASE_URL set, else SQLite)
    summary_primary = database.migrate_and_deduplicate_phones()
    print("[Primary DB Migration Result]:")
    print(json.dumps(summary_primary, indent=2))

    # 2. If PostgreSQL was primary, also initialize & migrate local SQLite fallback DB if present
    if database.DATABASE_URL and database.DB_PATH.exists():
        orig_url = database.DATABASE_URL
        try:
            database.DATABASE_URL = ""
            summary_sqlite = database.migrate_and_deduplicate_phones()
            print("[Local SQLite Fallback Migration Result]:")
            print(json.dumps(summary_sqlite, indent=2))
        finally:
            database.DATABASE_URL = orig_url

    print("=== Migration Complete ===")


if __name__ == "__main__":
    main()
