import hashlib
import random
import sqlite3
import string
import threading
from datetime import datetime, timedelta

from src.config import DB_PATH, FREE_TRIAL_QUESTIONS, DATABASE_URL
from src.phone_utils import (
    is_canonical_pk_phone,
    is_synthetic_user_id,
    normalize_email,
    normalize_pk_phone,
    normalize_pk_phone_or_none,
)

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    ThreadedConnectionPool = None

_PG_POOL_LOCK = threading.Lock()
_PG_POOLS: dict[str, "ThreadedConnectionPool"] = {}


def _get_pg_pool(dsn: str):
    """Returns a thread-safe warm PostgreSQL connection pool for the given DSN."""
    pool = _PG_POOLS.get(dsn)
    if pool is not None and not pool.closed:
        return pool
    with _PG_POOL_LOCK:
        pool = _PG_POOLS.get(dsn)
        if pool is not None and not pool.closed:
            return pool
        pool = ThreadedConnectionPool(
            minconn=1,
            maxconn=8,
            dsn=dsn,
            cursor_factory=RealDictCursor,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5,
        )
        _PG_POOLS[dsn] = pool
        return pool


def _acquire_pg_conn(dsn: str):
    pool = _get_pg_pool(dsn)
    conn = pool.getconn()
    if conn.closed != 0:
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()
    conn.autocommit = True
    return pool, conn


class DBContext:
    """Universal connection manager supporting pooled Supabase PostgreSQL and SQLite."""
    def __init__(self):
        self.conn = None
        self.cursor = None
        self.is_pg = False
        self._pg_pool = None
        self._pg_dsn = None
        self._conn_broken = False

    def __enter__(self):
        if DATABASE_URL and HAS_PSYCOPG2:
            try:
                self._pg_dsn = DATABASE_URL
                self._pg_pool, self.conn = _acquire_pg_conn(DATABASE_URL)
                self.is_pg = True
                self.cursor = self.conn.cursor()
                return self
            except Exception as e:
                print(f"[DB Warning] Could not connect to Supabase PostgreSQL: {e}. Falling back to SQLite.")
                self._pg_pool = None
                self.conn = None

        self.conn = sqlite3.connect(str(DB_PATH))
        self.conn.row_factory = sqlite3.Row
        self.is_pg = False
        self.cursor = self.conn.cursor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if not self.is_pg:
                if exc_type is None and self.conn:
                    self.conn.commit()
                if self.conn:
                    self.conn.close()
            else:
                if self.cursor:
                    try:
                        self.cursor.close()
                    except Exception:
                        pass
                if self.conn:
                    discard = self._conn_broken or exc_type is not None or self.conn.closed != 0
                    if self._pg_pool is not None and not self._pg_pool.closed:
                        self._pg_pool.putconn(self.conn, close=discard)
                    else:
                        self.conn.close()
        except Exception:
            pass

    def _reconnect_pg(self):
        """Discards a stale pooled connection (e.g. idle timeout) and acquires a fresh one."""
        if self.cursor:
            try:
                self.cursor.close()
            except Exception:
                pass
        if self.conn and self._pg_pool is not None and not self._pg_pool.closed:
            try:
                self._pg_pool.putconn(self.conn, close=True)
            except Exception:
                pass
        self._pg_pool, self.conn = _acquire_pg_conn(self._pg_dsn)
        self.cursor = self.conn.cursor()
        self._conn_broken = False

    def execute(self, query: str, params: tuple = ()):
        if self.is_pg:
            pg_query = query.replace("?", "%s")
            try:
                self.cursor.execute(pg_query, params)
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Transparent single retry on stale pooled connection
                self._conn_broken = True
                self._reconnect_pg()
                self.cursor.execute(pg_query, params)
        else:
            self.cursor.execute(query, params)
        return self.cursor

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


def _parse_dt(val) -> datetime | None:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except Exception:
        return None


def _db_ts(db: DBContext, dt: datetime):
    return dt if db.is_pg else dt.isoformat()


def _generate_unique_referral_code(db: DBContext) -> str:
    """Generates a random unique referral code with HASSAN- prefix."""
    chars = string.ascii_uppercase + string.digits
    while True:
        suffix = "".join(random.choices(chars, k=4))
        code = f"HASSAN-{suffix}"
        db.execute("SELECT 1 FROM users WHERE referral_code = ?", (code,))
        if not db.fetchone():
            return code


def _generate_email_user_id(db: DBContext, norm_email: str) -> str:
    """Generates a stable synthetic primary key for an email-authenticated user."""
    base = f"u_{hashlib.sha256(norm_email.encode('utf-8')).hexdigest()[:12]}"
    candidate = base
    counter = 1
    while True:
        db.execute("SELECT 1 FROM users WHERE phone_number = ?", (candidate,))
        if not db.fetchone():
            return candidate
        candidate = f"{base}_{counter}"
        counter += 1


def _serialize_user_row(row) -> dict:
    user_dict = dict(row)
    if not user_dict.get("tier"):
        user_dict["tier"] = "basic"

    # Populate canonical contact_phone if missing on a phone-keyed user
    cp = user_dict.get("contact_phone")
    if cp:
        cp_norm = normalize_pk_phone_or_none(cp)
        user_dict["contact_phone"] = cp_norm or cp
    elif is_canonical_pk_phone(user_dict.get("phone_number")):
        user_dict["contact_phone"] = user_dict["phone_number"]
    else:
        user_dict["contact_phone"] = ""

    expiry_dt = _parse_dt(user_dict.get("subscription_expiry"))
    user_dict["is_subscribed"] = bool(expiry_dt and expiry_dt > datetime.now())

    for ts_key in ["subscription_expiry", "created_at", "last_active"]:
        if isinstance(user_dict.get(ts_key), datetime):
            user_dict[ts_key] = user_dict[ts_key].isoformat()

    return user_dict


def _find_user_row(db: DBContext, identifier: str):
    """
    Finds an existing user by:
      1. Exact primary key (phone_number / synthetic ID)
      2. Lowercased email
      3. Canonical Pakistani mobile number (matching phone_number OR contact_phone)
    """
    ident = str(identifier or "").strip()
    if not ident:
        return None

    db.execute("SELECT * FROM users WHERE phone_number = ?", (ident,))
    row = db.fetchone()
    if row:
        return row

    if "@" in ident:
        norm_email = normalize_email(ident)
        db.execute(
            "SELECT * FROM users WHERE LOWER(email) = ? AND email IS NOT NULL AND email != ''",
            (norm_email,),
        )
        row = db.fetchone()
        if row:
            return row

    canon = normalize_pk_phone_or_none(ident)
    if canon:
        db.execute(
            "SELECT * FROM users WHERE phone_number = ? OR contact_phone = ?",
            (canon, canon),
        )
        row = db.fetchone()
        if row:
            return row

    return None


def init_db():
    """Initializes schemas and additive columns on either Supabase PostgreSQL or SQLite."""
    with DBContext() as db:
        if db.is_pg:
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    phone_number TEXT PRIMARY KEY,
                    name TEXT DEFAULT 'User',
                    tier TEXT DEFAULT 'basic',
                    referral_code TEXT UNIQUE,
                    referred_by TEXT,
                    referral_count INTEGER DEFAULT 0,
                    referral_credited INTEGER DEFAULT 0,
                    welcome_sent INTEGER DEFAULT 0,
                    coaching_pitch_sent INTEGER DEFAULT 0,
                    trial_questions_used INTEGER DEFAULT 0,
                    subscription_expiry TIMESTAMP,
                    total_queries INTEGER DEFAULT 0,
                    email TEXT,
                    google_id TEXT,
                    avatar_url TEXT,
                    auth_provider TEXT DEFAULT 'phone',
                    contact_phone TEXT,
                    created_at TIMESTAMP,
                    last_active TIMESTAMP
                );
            """)
            db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS contact_phone TEXT;")
            db.execute("CREATE INDEX IF NOT EXISTS users_contact_phone_idx ON users(contact_phone);")
            db.execute("CREATE INDEX IF NOT EXISTS users_email_lower_idx ON users(LOWER(email));")

            db.execute("""
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id SERIAL PRIMARY KEY,
                    phone_number TEXT,
                    trx_id TEXT UNIQUE,
                    tier TEXT DEFAULT 'basic',
                    plan_selected TEXT DEFAULT 'basic',
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP
                );
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS coaching_leads (
                    id SERIAL PRIMARY KEY,
                    phone_number TEXT,
                    tier TEXT,
                    total_queries INTEGER,
                    status TEXT DEFAULT 'new',
                    created_at TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS chat_history (
                    id SERIAL PRIMARY KEY,
                    phone_number TEXT,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS chat_history_phone_idx ON chat_history(phone_number, id DESC);
            """)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    phone_number TEXT PRIMARY KEY,
                    name TEXT DEFAULT 'User',
                    tier TEXT DEFAULT 'basic',
                    referral_code TEXT UNIQUE,
                    referred_by TEXT,
                    referral_count INTEGER DEFAULT 0,
                    referral_credited INTEGER DEFAULT 0,
                    welcome_sent INTEGER DEFAULT 0,
                    coaching_pitch_sent INTEGER DEFAULT 0,
                    trial_questions_used INTEGER DEFAULT 0,
                    subscription_expiry TIMESTAMP,
                    total_queries INTEGER DEFAULT 0,
                    email TEXT,
                    google_id TEXT,
                    avatar_url TEXT,
                    auth_provider TEXT DEFAULT 'phone',
                    contact_phone TEXT,
                    created_at TIMESTAMP,
                    last_active TIMESTAMP
                )
            """)
            db.execute("PRAGMA table_info(users)")
            existing_cols = {r["name"] for r in db.fetchall()}
            if "contact_phone" not in existing_cols:
                db.execute("ALTER TABLE users ADD COLUMN contact_phone TEXT")

            db.execute("CREATE INDEX IF NOT EXISTS users_contact_phone_idx ON users(contact_phone)")
            db.execute("CREATE INDEX IF NOT EXISTS users_email_idx ON users(email)")

            db.execute("""
                CREATE TABLE IF NOT EXISTS payment_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT,
                    trx_id TEXT UNIQUE,
                    tier TEXT DEFAULT 'basic',
                    plan_selected TEXT DEFAULT 'basic',
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS coaching_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT,
                    tier TEXT,
                    total_queries INTEGER,
                    status TEXT DEFAULT 'new',
                    created_at TIMESTAMP
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phone_number TEXT,
                    role TEXT,
                    content TEXT,
                    created_at TIMESTAMP
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS chat_history_phone_idx ON chat_history(phone_number, id DESC)
            """)


def get_user_by_identifier(identifier: str) -> dict | None:
    """Read-only user lookup by user_id, email, or any Pakistani phone format."""
    if not identifier:
        return None
    with DBContext() as db:
        row = _find_user_row(db, identifier)
        if not row:
            return None
        user_dict = _serialize_user_row(row)
        if not user_dict.get("referral_code"):
            new_code = _generate_unique_referral_code(db)
            db.execute("UPDATE users SET referral_code = ? WHERE phone_number = ?", (new_code, user_dict["phone_number"]))
            user_dict["referral_code"] = new_code
        return user_dict


def get_or_create_user(phone_number: str) -> dict:
    """Finds or creates a user, always normalizing Pakistani mobile numbers to 923XXXXXXXXX."""
    now = datetime.now()
    ident = str(phone_number or "").strip()

    with DBContext() as db:
        row = _find_user_row(db, ident)
        if not row:
            ref_code = _generate_unique_referral_code(db)
            ts = _db_ts(db, now)

            if "@" in ident:
                norm_email = normalize_email(ident)
                synth_id = _generate_email_user_id(db, norm_email)
                default_name = norm_email.split("@")[0]
                db.execute("""
                    INSERT INTO users (
                        phone_number, email, name, tier, referral_code, referral_count,
                        referral_credited, welcome_sent, trial_questions_used,
                        created_at, last_active, auth_provider
                    )
                    VALUES (?, ?, ?, 'basic', ?, 0, 0, 0, 0, ?, ?, 'email')
                """, (synth_id, norm_email, default_name, ref_code, ts, ts))
                target_key = synth_id
            else:
                canon = normalize_pk_phone_or_none(ident)
                target_key = canon if canon else ident
                contact_val = canon if canon else None
                db.execute("""
                    INSERT INTO users (
                        phone_number, contact_phone, tier, referral_code, referral_count,
                        referral_credited, welcome_sent, trial_questions_used,
                        created_at, last_active, auth_provider
                    )
                    VALUES (?, ?, 'basic', ?, 0, 0, 0, 0, ?, ?, 'phone')
                """, (target_key, contact_val, ref_code, ts, ts))

            db.execute("SELECT * FROM users WHERE phone_number = ?", (target_key,))
            row = db.fetchone()

        user_dict = _serialize_user_row(row)
        if not user_dict.get("referral_code"):
            new_code = _generate_unique_referral_code(db)
            db.execute("UPDATE users SET referral_code = ? WHERE phone_number = ?", (new_code, user_dict["phone_number"]))
            user_dict["referral_code"] = new_code

        return user_dict


def get_or_create_google_user(google_id: str, email: str, name: str = "User", avatar_url: str = "") -> dict:
    """Gets or registers a user via cryptographically verified Google OAuth."""
    norm_email = normalize_email(email)
    clean_name = (name or "").strip() or (norm_email.split("@")[0] if norm_email else "User")
    now = datetime.now()

    with DBContext() as db:
        ts = _db_ts(db, now)
        db.execute(
            """
            SELECT * FROM users
            WHERE (google_id = ? AND google_id IS NOT NULL AND google_id != '')
               OR (LOWER(email) = ? AND email IS NOT NULL AND email != '')
            """,
            (google_id, norm_email),
        )
        row = db.fetchone()

        if row:
            user_id = row["phone_number"]
            existing_name = (row["name"] or "").strip()
            final_name = clean_name if (not existing_name or existing_name == "User") else existing_name
            db.execute("""
                UPDATE users
                SET last_active = ?,
                    google_id = COALESCE(NULLIF(google_id, ''), ?),
                    email = COALESCE(NULLIF(email, ''), ?),
                    avatar_url = CASE WHEN ? != '' THEN ? ELSE avatar_url END,
                    name = ?
                WHERE phone_number = ?
            """, (ts, google_id, norm_email, avatar_url, avatar_url, final_name, user_id))
            db.execute("SELECT * FROM users WHERE phone_number = ?", (user_id,))
            return _serialize_user_row(db.fetchone())

        synth_id = _generate_email_user_id(db, norm_email) if norm_email else f"google_{google_id[-10:]}"
        ref_code = _generate_unique_referral_code(db)
        db.execute("""
            INSERT INTO users (
                phone_number, name, email, google_id, avatar_url, auth_provider,
                tier, referral_code, referral_count, referral_credited,
                welcome_sent, trial_questions_used, created_at, last_active
            )
            VALUES (?, ?, ?, ?, ?, 'google', 'basic', ?, 0, 0, 0, 0, ?, ?)
        """, (synth_id, clean_name, norm_email, google_id, avatar_url, ref_code, ts, ts))
        db.execute("SELECT * FROM users WHERE phone_number = ?", (synth_id,))
        return _serialize_user_row(db.fetchone())


def link_phone_to_user(user_id: str, raw_phone: str) -> tuple[bool, str, str]:
    """
    Normalizes a Pakistani mobile number to 923XXXXXXXXX and saves it on the verified user's
    contact_phone column while keeping their primary key stable (no orphaned sessions/history).
    Blocks claiming a number that already belongs to another user.
    """
    ok, canon, err = normalize_pk_phone(raw_phone)
    if not ok:
        return False, "", err

    now = datetime.now()
    with DBContext() as db:
        row = _find_user_row(db, user_id)
        if not row:
            return False, "", "User account not found."
        actual_uid = row["phone_number"]

        # Reject if another account already owns this canonical phone number
        db.execute(
            """
            SELECT phone_number FROM users
            WHERE phone_number != ?
              AND (phone_number = ? OR contact_phone = ?)
            """,
            (actual_uid, canon, canon),
        )
        conflict = db.fetchone()
        if conflict:
            return (
                False,
                "",
                "This Pakistani mobile number is already linked to another account. "
                "Please use your own number or contact support.",
            )

        db.execute(
            "UPDATE users SET contact_phone = ?, last_active = ? WHERE phone_number = ?",
            (canon, _db_ts(db, now), actual_uid),
        )
        return True, canon, "Mobile number saved."


def link_phone_to_google_user(identifier: str, phone: str) -> bool:
    """Backwards-compatible wrapper around link_phone_to_user."""
    ok, _, _ = link_phone_to_user(identifier, phone)
    return ok


# ── Business Logic (All Canonicalized via Resolved user_id) ───────────────

def apply_referral_code(phone_number: str, code: str) -> tuple[bool, str]:
    code_clean = code.strip().upper()
    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]

    if user.get("referral_code") == code_clean:
        return False, "You cannot use your own referral code."

    if user.get("referred_by"):
        return False, f"You have already applied referral code {user['referred_by']}."

    with DBContext() as db:
        db.execute("SELECT phone_number FROM users WHERE UPPER(referral_code) = ?", (code_clean,))
        referrer_row = db.fetchone()
        if not referrer_row:
            return False, f"Referral code '{code_clean}' was not found. Please verify and try again."

        db.execute("UPDATE users SET referred_by = ? WHERE phone_number = ?", (code_clean, user_id))

    return True, f"Referral code '{code_clean}' applied! Once your plan is activated, your friend will get credit towards 1 free month."


def get_referral_stats(phone_number: str) -> dict:
    user = get_or_create_user(phone_number)
    code = user.get("referral_code", "UNKNOWN")
    count = user.get("referral_count", 0) or 0
    cycle_progress = count % 3
    needed = 3 - cycle_progress if cycle_progress != 0 else (0 if count > 0 else 3)
    return {
        "code": code,
        "count": count,
        "needed": needed,
        "cycle_progress": cycle_progress,
    }


def evaluate_user_access(user: dict) -> tuple[bool, str, str]:
    """Pure in-memory access evaluation from an already-fetched user dict (zero DB round trips)."""
    expiry = _parse_dt(user.get("subscription_expiry"))
    if expiry and expiry > datetime.now():
        return True, "subscribed", "pro"

    used = user.get("trial_questions_used", 0) or 0
    if used < FREE_TRIAL_QUESTIONS:
        return True, "free_trial", "trial"

    return False, "paywall_required", "trial"


def can_user_chat(phone_number: str) -> tuple[bool, str, str]:
    user = get_or_create_user(phone_number)
    return evaluate_user_access(user)


def record_user_query(phone_number: str):
    now = datetime.now()
    with DBContext() as db:
        row = _find_user_row(db, phone_number)
        if row:
            user_id = row["phone_number"]
            db.execute("""
                UPDATE users SET
                trial_questions_used = trial_questions_used + 1,
                total_queries = total_queries + 1,
                last_active = ?
                WHERE phone_number = ?
            """, (_db_ts(db, now), user_id))
            return

    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    with DBContext() as db:
        db.execute("""
            UPDATE users SET
            trial_questions_used = trial_questions_used + 1,
            total_queries = total_queries + 1,
            last_active = ?
            WHERE phone_number = ?
        """, (_db_ts(db, now), user_id))


def mark_welcome_sent(phone_number: str):
    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    with DBContext() as db:
        db.execute("UPDATE users SET welcome_sent = 1 WHERE phone_number = ?", (user_id,))


def submit_trx_id(phone_number: str, trx_id: str, plan_selected: str = "pro") -> bool:
    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    now = datetime.now()
    # Single unified all-access monthly plan
    plan_clean = "pro"

    with DBContext() as db:
        try:
            db.execute("""
                INSERT INTO payment_requests (phone_number, trx_id, plan_selected, tier, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
            """, (user_id, trx_id, plan_clean, plan_clean, _db_ts(db, now)))
            return True
        except Exception:
            return False


def activate_user(phone_number: str, days: int = 30, tier: str = "pro") -> tuple[datetime, str, str, dict | None]:
    # Single unified all-access monthly plan
    tier_clean = "pro"

    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    now = datetime.now()
    curr_exp = _parse_dt(user.get("subscription_expiry"))
    base_time = max(curr_exp, now) if curr_exp else now
    new_expiry = base_time + timedelta(days=days)
    referrer_reward = None

    with DBContext() as db:
        db.execute("""
            UPDATE users
            SET subscription_expiry = ?, tier = ?
            WHERE phone_number = ?
        """, (_db_ts(db, new_expiry), tier_clean, user_id))

        db.execute("""
            UPDATE payment_requests
            SET status = 'approved'
            WHERE phone_number = ? AND status = 'pending'
        """, (user_id,))

        # Referral reward handling
        if user.get("referred_by") and not user.get("referral_credited"):
            ref_code = user["referred_by"].upper()
            db.execute("SELECT * FROM users WHERE UPPER(referral_code) = ?", (ref_code,))
            ref_row = db.fetchone()
            if ref_row:
                ref_user = dict(ref_row)
                new_count = (ref_user.get("referral_count") or 0) + 1
                ref_uid = ref_user["phone_number"]

                db.execute("UPDATE users SET referral_credited = 1 WHERE phone_number = ?", (user_id,))

                if new_count % 3 == 0:
                    ref_curr_exp = _parse_dt(ref_user.get("subscription_expiry"))
                    ref_base = max(ref_curr_exp, now) if ref_curr_exp else now
                    ref_new_exp = ref_base + timedelta(days=30)
                    db.execute("""
                        UPDATE users
                        SET referral_count = ?, subscription_expiry = ?
                        WHERE phone_number = ?
                    """, (new_count, _db_ts(db, ref_new_exp), ref_uid))

                    ref_contact = ref_user.get("contact_phone") or (
                        ref_uid if is_canonical_pk_phone(ref_uid) else ""
                    )
                    referrer_reward = {
                        "referrer_phone": ref_contact or ref_uid,
                        "new_expiry": ref_new_exp,
                        "referral_count": new_count,
                    }
                else:
                    db.execute("UPDATE users SET referral_count = ? WHERE phone_number = ?", (new_count, ref_uid))

    user_updated = get_or_create_user(user_id)
    return new_expiry, tier_clean, user_updated.get("referral_code", ""), referrer_reward


def mark_coaching_pitch_sent(phone_number: str):
    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    with DBContext() as db:
        db.execute("UPDATE users SET coaching_pitch_sent = 1 WHERE phone_number = ?", (user_id,))


def record_coaching_lead(phone_number: str) -> tuple[bool, str]:
    user = get_or_create_user(phone_number)
    user_id = user["phone_number"]
    tier = user.get("tier", "basic")
    queries = user.get("total_queries", 0)
    now = datetime.now()

    with DBContext() as db:
        db.execute("""
            SELECT id FROM coaching_leads
            WHERE phone_number = ? AND status = 'new'
        """, (user_id,))
        if db.fetchone():
            return False, "You have already requested a coaching session. Our team will contact you shortly!"

        db.execute("""
            INSERT INTO coaching_leads (phone_number, tier, total_queries, status, created_at)
            VALUES (?, ?, ?, 'new', ?)
        """, (user_id, tier, queries, _db_ts(db, now)))

    return True, "Your 1-on-1 coaching request has been registered! Our team will contact you on WhatsApp with scheduling details."


def get_system_metrics() -> dict:
    from src.config import MONTHLY_PRICE_PKR
    now = datetime.now()

    with DBContext() as db:
        now_ts = _db_ts(db, now)

        # 1. Total Users
        db.execute("SELECT COUNT(*) AS count FROM users")
        total_users = db.fetchone()["count"]

        # 2. Subscribers (Unified All-Access Monthly Plan)
        db.execute("SELECT COUNT(*) AS count FROM users WHERE subscription_expiry > ?", (now_ts,))
        active_subscribers = db.fetchone()["count"]

        # 3. Trials
        db.execute(
            "SELECT COUNT(*) AS count FROM users WHERE (subscription_expiry IS NULL OR subscription_expiry <= ?) AND trial_questions_used < ?",
            (now_ts, FREE_TRIAL_QUESTIONS),
        )
        active_trials = db.fetchone()["count"]

        db.execute(
            "SELECT COUNT(*) AS count FROM users WHERE (subscription_expiry IS NULL OR subscription_expiry <= ?) AND trial_questions_used >= ?",
            (now_ts, FREE_TRIAL_QUESTIONS),
        )
        paywalled_trials = db.fetchone()["count"]

        # 4. Conversion & Revenue
        conversion_rate = round((active_subscribers / total_users * 100), 1) if total_users > 0 else 0.0
        estimated_mrr_pkr = active_subscribers * MONTHLY_PRICE_PKR

        # 5. Queries & Referrals
        db.execute("SELECT SUM(total_queries) AS total_q, SUM(referral_count) AS total_ref FROM users")
        row_totals = db.fetchone()
        total_queries = row_totals["total_q"] or 0
        total_referrals = row_totals["total_ref"] or 0

        # 6. Pending Payments
        db.execute("SELECT COUNT(*) AS count FROM payment_requests WHERE status = 'pending'")
        pending_payments = db.fetchone()["count"]

        # Build lookup map for enriching payments with contact_phone / email / name
        db.execute("SELECT phone_number, name, email, contact_phone FROM users")
        user_lookup = {}
        for ur in db.fetchall():
            ud = dict(ur)
            cp = normalize_pk_phone_or_none(ud.get("contact_phone")) or (
                ud["phone_number"] if is_canonical_pk_phone(ud["phone_number"]) else ""
            )
            user_lookup[ud["phone_number"]] = {
                "name": ud.get("name") or "",
                "email": ud.get("email") or "",
                "contact_phone": cp,
            }

        # 7. Recent Transactions (last 15)
        db.execute("SELECT * FROM payment_requests ORDER BY id DESC LIMIT 15")
        recent_payments = []
        for r in db.fetchall():
            d = dict(r)
            if isinstance(d.get("created_at"), datetime):
                d["created_at"] = d["created_at"].isoformat()
            meta = user_lookup.get(d["phone_number"], {})
            d["contact_phone"] = meta.get("contact_phone") or (
                d["phone_number"] if is_canonical_pk_phone(d["phone_number"]) else ""
            )
            d["email"] = meta.get("email", "")
            d["user_name"] = meta.get("name", "")
            recent_payments.append(d)

        # 8. All Users list (up to 50 most active)
        db.execute("""
            SELECT phone_number, name, email, contact_phone, auth_provider, tier,
                   trial_questions_used, subscription_expiry, total_queries,
                   referral_code, referral_count, created_at, last_active
            FROM users
            ORDER BY last_active DESC NULLS LAST LIMIT 50
        """ if db.is_pg else """
            SELECT phone_number, name, email, contact_phone, auth_provider, tier,
                   trial_questions_used, subscription_expiry, total_queries,
                   referral_code, referral_count, created_at, last_active
            FROM users
            ORDER BY last_active DESC LIMIT 50
        """)
        all_users = [_serialize_user_row(r) for r in db.fetchall()]

        # 10. Real 14-day daily activity series from database timestamps (zero mock points)
        day_keys = [(now.date() - timedelta(days=13 - i)) for i in range(14)]
        chart_dates = [d.strftime("%b %d") for d in day_keys[:-1]] + ["Today"]
        day_index = {d.isoformat(): idx for idx, d in enumerate(day_keys)}
        window_start = datetime.combine(day_keys[0], datetime.min.time())

        chart_queries = [0] * 14
        db.execute(
            "SELECT created_at FROM chat_history WHERE role = 'assistant' AND created_at >= ?",
            (_db_ts(db, window_start),),
        )
        for row in db.fetchall():
            dt = _parse_dt(row["created_at"])
            if dt:
                idx = day_index.get(dt.date().isoformat())
                if idx is not None:
                    chart_queries[idx] += 1

        chart_new_users = [0] * 14
        db.execute(
            "SELECT created_at FROM users WHERE created_at >= ?",
            (_db_ts(db, window_start),),
        )
        for row in db.fetchall():
            dt = _parse_dt(row["created_at"])
            if dt:
                idx = day_index.get(dt.date().isoformat())
                if idx is not None:
                    chart_new_users[idx] += 1

        db_backend = "Supabase PostgreSQL" if db.is_pg else "Local SQLite"

    return {
        "total_users": total_users,
        "active_subscribers": active_subscribers,
        "monthly_price_pkr": MONTHLY_PRICE_PKR,
        "active_trials": active_trials,
        "paywalled_trials": paywalled_trials,
        "conversion_rate": conversion_rate,
        "estimated_mrr_pkr": estimated_mrr_pkr,
        "total_queries": total_queries,
        "total_referrals": total_referrals,
        "pending_payments": pending_payments,
        "recent_payments": recent_payments,
        "all_users": all_users,
        "chart_dates": chart_dates,
        "chart_queries": chart_queries,
        "chart_new_users": chart_new_users,
        "db_backend": db_backend,
    }


def get_subscriber_phones(tier: str = "all") -> list[str]:
    """Returns canonical 923XXXXXXXXX numbers for all active subscribers (skips unlinked synthetic IDs)."""
    now = datetime.now()
    with DBContext() as db:
        now_ts = _db_ts(db, now)
        db.execute(
            "SELECT phone_number, contact_phone FROM users WHERE subscription_expiry > ?",
            (now_ts,),
        )

        phones = []
        seen = set()
        for row in db.fetchall():
            d = dict(row)
            cand = normalize_pk_phone_or_none(d.get("contact_phone")) or normalize_pk_phone_or_none(d.get("phone_number"))
            if cand and cand not in seen:
                seen.add(cand)
                phones.append(cand)
        return phones


def save_chat_message(phone_number: str, role: str, content: str):
    """Saves a message turn (user or assistant) for contextual memory under canonical user_id."""
    try:
        now = datetime.now()
        with DBContext() as db:
            row = _find_user_row(db, phone_number)
            if row:
                user_id = row["phone_number"]
                db.execute("""
                    INSERT INTO chat_history (phone_number, role, content, created_at)
                    VALUES (?, ?, ?, ?)
                """, (user_id, role, content, _db_ts(db, now)))
                return
        user = get_or_create_user(phone_number)
        user_id = user["phone_number"]
        with DBContext() as db:
            db.execute("""
                INSERT INTO chat_history (phone_number, role, content, created_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, role, content, _db_ts(db, now)))
    except Exception as e:
        print(f"[Warning] Failed to save chat message: {e}")


def complete_chat_turn(user_id: str, user_text: str, ai_reply: str) -> dict:
    """
    Saves both user + assistant messages, increments usage counters, and returns the updated
    serialized user row in a SINGLE pooled database connection (eliminates 4 separate round trips).
    """
    now = datetime.now()
    with DBContext() as db:
        ts = _db_ts(db, now)
        row = _find_user_row(db, user_id)
        if not row:
            # Rare fallback: ensure user exists first
            pass
        else:
            canonical_uid = row["phone_number"]
            db.execute("""
                INSERT INTO chat_history (phone_number, role, content, created_at)
                VALUES (?, 'user', ?, ?)
            """, (canonical_uid, user_text, ts))
            db.execute("""
                INSERT INTO chat_history (phone_number, role, content, created_at)
                VALUES (?, 'assistant', ?, ?)
            """, (canonical_uid, ai_reply, ts))
            db.execute("""
                UPDATE users SET
                trial_questions_used = trial_questions_used + 1,
                total_queries = total_queries + 1,
                last_active = ?
                WHERE phone_number = ?
            """, (ts, canonical_uid))
            db.execute("SELECT * FROM users WHERE phone_number = ?", (canonical_uid,))
            updated = db.fetchone()
            if updated:
                return _serialize_user_row(updated)

    save_chat_message(user_id, "user", user_text)
    save_chat_message(user_id, "assistant", ai_reply)
    record_user_query(user_id)
    return get_or_create_user(user_id)


def get_recent_chat_history(phone_number: str, limit: int = 6) -> list[dict]:
    """Retrieves the recent conversation turns in chronological order in a single DB round trip."""
    try:
        with DBContext() as db:
            row = _find_user_row(db, phone_number)
            if not row:
                return []
            user_id = row["phone_number"]
            db.execute("""
                SELECT role, content FROM chat_history
                WHERE phone_number = ?
                ORDER BY id DESC
                LIMIT ?
            """, (user_id, limit))
            rows = db.fetchall()
            if not rows:
                return []
            return [dict(r) for r in reversed(rows)]
    except Exception as e:
        print(f"[Warning] Failed to get chat history: {e}")
        return []


def get_chat_conversations(limit_users: int = 100, messages_per_user: int = 100) -> list[dict]:
    """
    Returns users with saved chat_history threads ordered by most recent message first.
    Works identically on both Supabase PostgreSQL and local SQLite in 3 lightweight queries.
    """
    try:
        with DBContext() as db:
            db.execute(
                """
                SELECT phone_number, MAX(id) AS last_id, COUNT(*) AS msg_count
                FROM chat_history
                GROUP BY phone_number
                ORDER BY last_id DESC
                LIMIT ?
                """,
                (limit_users,),
            )
            thread_rows = [dict(r) for r in db.fetchall()]
            if not thread_rows:
                return []

            user_ids = [r["phone_number"] for r in thread_rows]
            placeholders = ", ".join(["?"] * len(user_ids))

            db.execute(
                f"""
                SELECT phone_number, name, email, contact_phone, auth_provider,
                       tier, trial_questions_used, subscription_expiry,
                       total_queries, created_at, last_active
                FROM users
                WHERE phone_number IN ({placeholders})
                """,
                tuple(user_ids),
            )
            user_map = {}
            for ur in db.fetchall():
                serialized = _serialize_user_row(ur)
                user_map[serialized["phone_number"]] = serialized

            db.execute(
                f"""
                SELECT id, phone_number, role, content, created_at
                FROM chat_history
                WHERE phone_number IN ({placeholders})
                ORDER BY id ASC
                """,
                tuple(user_ids),
            )
            msgs_by_user: dict[str, list[dict]] = {uid: [] for uid in user_ids}
            for mr in db.fetchall():
                md = dict(mr)
                if isinstance(md.get("created_at"), datetime):
                    md["created_at"] = md["created_at"].isoformat()
                else:
                    md["created_at"] = str(md.get("created_at") or "")
                msgs_by_user.setdefault(md["phone_number"], []).append(
                    {
                        "id": md["id"],
                        "role": md.get("role") or "user",
                        "content": md.get("content") or "",
                        "created_at": md["created_at"],
                    }
                )

            conversations = []
            for tr in thread_rows:
                uid = tr["phone_number"]
                u = user_map.get(uid, {})
                all_msgs = msgs_by_user.get(uid, [])
                trimmed_msgs = all_msgs[-messages_per_user:] if messages_per_user else all_msgs
                user_msgs = [m for m in all_msgs if m["role"] == "user"]
                last_msg = all_msgs[-1] if all_msgs else None
                last_user_msg = user_msgs[-1] if user_msgs else last_msg

                canon_phone = u.get("contact_phone") or (
                    uid if is_canonical_pk_phone(uid) else ""
                )
                conversations.append(
                    {
                        "user_id": uid,
                        "name": (u.get("name") or "").strip() or "User",
                        "email": (u.get("email") or "").strip(),
                        "contact_phone": canon_phone,
                        "auth_provider": u.get("auth_provider") or "phone",
                        "is_subscribed": bool(u.get("is_subscribed", False)),
                        "trial_questions_used": u.get("trial_questions_used", 0) or 0,
                        "total_queries": u.get("total_queries", 0) or 0,
                        "message_count": len(all_msgs),
                        "user_message_count": len(user_msgs),
                        "last_message_at": (last_msg.get("created_at") if last_msg else "") or (u.get("last_active") or ""),
                        "last_user_preview": (last_user_msg.get("content") if last_user_msg else "")[:140],
                        "messages": trimmed_msgs,
                    }
                )
            return conversations
    except Exception as e:
        print(f"[Warning] Failed to get chat conversations: {e}")
        return []


def delete_user_completely(identifier: str) -> tuple[bool, dict | str]:
    """
    Permanently deletes a user and ALL associated records across `chat_history`,
    `payment_requests`, `coaching_leads`, `email_otps` (if present), and `users`.
    Accepts canonical phone, any Pakistani mobile format, email, or synthetic user ID.
    """
    ident = str(identifier or "").strip()
    if not ident:
        return False, "Please provide a valid phone number, email, or user ID."

    with DBContext() as db:
        row = _find_user_row(db, ident)
        if not row:
            return False, f"User '{ident}' was not found in the database."

        user_dict = dict(row)
        uid = str(user_dict["phone_number"])
        contact_phone = normalize_pk_phone_or_none(user_dict.get("contact_phone"))
        email = normalize_email(user_dict.get("email") or "")
        name = (user_dict.get("name") or "").strip() or "User"

        # Collect all keys under which child rows could exist
        child_keys = [uid]
        if contact_phone and contact_phone != uid:
            child_keys.append(contact_phone)
        placeholders = ", ".join(["?"] * len(child_keys))

        deleted_counts: dict[str, int] = {}
        for child_tbl in ("chat_history", "payment_requests", "coaching_leads"):
            db.execute(
                f"DELETE FROM {child_tbl} WHERE phone_number IN ({placeholders})",
                tuple(child_keys),
            )
            deleted_counts[child_tbl] = max(0, db.cursor.rowcount or 0)

        # Clean up legacy email_otps if that table exists in this database
        otp_deleted = 0
        if email:
            try:
                db.execute("DELETE FROM email_otps WHERE LOWER(email) = ?", (email,))
                otp_deleted = max(0, db.cursor.rowcount or 0)
            except Exception:
                otp_deleted = 0
        deleted_counts["email_otps"] = otp_deleted

        db.execute("DELETE FROM users WHERE phone_number = ?", (uid,))
        deleted_counts["users"] = max(0, db.cursor.rowcount or 0)

        return True, {
            "user_id": uid,
            "name": name,
            "email": email,
            "contact_phone": contact_phone or "",
            "deleted_counts": deleted_counts,
        }


# ── Idempotent Migration & Deduplication ───────────────────────────────────

def migrate_and_deduplicate_phones() -> dict:
    """
    Normalizes all existing Pakistani phone rows in `users`, `payment_requests`,
    `coaching_leads`, and `chat_history` to canonical '923XXXXXXXXX', merging any
    duplicate rows while preserving the highest tier, latest subscription expiry,
    combined usage/referrals, and all child records.
    """
    init_db()
    summary = {
        "renamed": [],
        "merged": [],
        "contact_phones_updated": 0,
        "child_rows_updated": 0,
        "skipped": [],
    }

    with DBContext() as db:
        db.execute("SELECT * FROM users")
        all_rows = [dict(r) for r in db.fetchall()]

        # Group non-synthetic phone rows by canonical Pakistani number
        groups: dict[str, list[dict]] = {}
        synthetic_rows: list[dict] = []

        for row in all_rows:
            pk = str(row["phone_number"])
            if is_synthetic_user_id(pk):
                synthetic_rows.append(row)
                continue
            canon = normalize_pk_phone_or_none(pk)
            if not canon:
                summary["skipped"].append(pk)
                continue
            groups.setdefault(canon, []).append(row)

        for canon, rows in groups.items():
            if len(rows) == 1:
                row = rows[0]
                old_pk = row["phone_number"]
                if old_pk != canon:
                    # In-place rename of PK and child records
                    for child_tbl in ("payment_requests", "coaching_leads", "chat_history"):
                        db.execute(
                            f"UPDATE {child_tbl} SET phone_number = ? WHERE phone_number = ?",
                            (canon, old_pk),
                        )
                        summary["child_rows_updated"] += max(0, db.cursor.rowcount or 0)
                    db.execute(
                        "UPDATE users SET phone_number = ?, contact_phone = ? WHERE phone_number = ?",
                        (canon, canon, old_pk),
                    )
                    summary["renamed"].append(f"{old_pk} -> {canon}")
                elif row.get("contact_phone") != canon:
                    db.execute("UPDATE users SET contact_phone = ? WHERE phone_number = ?", (canon, canon))
                    summary["contact_phones_updated"] += 1
                continue

            # Multiple rows map to the same canonical number -> merge them cleanly
            rows_sorted = sorted(
                rows,
                key=lambda r: (
                    0 if r["phone_number"] == canon else 1,
                    0 if (r.get("tier") or "").lower() == "pro" else 1,
                    -(r.get("total_queries") or 0),
                ),
            )
            primary = rows_sorted[0]
            others = rows_sorted[1:]

            # Compute merged attributes
            tier = "pro" if any((r.get("tier") or "").lower() == "pro" for r in rows_sorted) else "basic"
            expiries = [dt for r in rows_sorted if (dt := _parse_dt(r.get("subscription_expiry")))]
            merged_expiry = max(expiries) if expiries else None
            created_list = [dt for r in rows_sorted if (dt := _parse_dt(r.get("created_at")))]
            merged_created = min(created_list) if created_list else None
            active_list = [dt for r in rows_sorted if (dt := _parse_dt(r.get("last_active")))]
            merged_active = max(active_list) if active_list else None

            merged_trial = sum((r.get("trial_questions_used") or 0) for r in rows_sorted)
            merged_queries = sum((r.get("total_queries") or 0) for r in rows_sorted)
            merged_ref_count = sum((r.get("referral_count") or 0) for r in rows_sorted)
            merged_ref_credited = max((r.get("referral_credited") or 0) for r in rows_sorted)
            merged_welcome = max((r.get("welcome_sent") or 0) for r in rows_sorted)
            merged_pitch = max((r.get("coaching_pitch_sent") or 0) for r in rows_sorted)

            merged_name = next(
                (r["name"] for r in rows_sorted if r.get("name") and r["name"].strip() != "User"),
                primary.get("name") or "User",
            )
            merged_email = next((normalize_email(r["email"]) for r in rows_sorted if r.get("email")), None)
            merged_google_id = next((r["google_id"] for r in rows_sorted if r.get("google_id")), None)
            merged_avatar = next((r["avatar_url"] for r in rows_sorted if r.get("avatar_url")), None)
            merged_referred_by = next((r["referred_by"] for r in rows_sorted if r.get("referred_by")), None)
            merged_ref_code = primary.get("referral_code") or next(
                (r["referral_code"] for r in rows_sorted if r.get("referral_code")), None
            )

            # Re-point child tables from ALL source keys to canon
            old_keys = [r["phone_number"] for r in rows_sorted]
            for old_k in old_keys:
                if old_k != canon:
                    for child_tbl in ("payment_requests", "coaching_leads", "chat_history"):
                        db.execute(
                            f"UPDATE {child_tbl} SET phone_number = ? WHERE phone_number = ?",
                            (canon, old_k),
                        )
                        summary["child_rows_updated"] += max(0, db.cursor.rowcount or 0)

            # Delete duplicate rows first (avoids UNIQUE conflicts on referral_code)
            for dup in others:
                db.execute("DELETE FROM users WHERE phone_number = ?", (dup["phone_number"],))

            # Rename primary if needed, then update with merged values
            primary_pk = primary["phone_number"]
            if primary_pk != canon:
                db.execute("UPDATE users SET phone_number = ? WHERE phone_number = ?", (canon, primary_pk))

            db.execute("""
                UPDATE users
                SET name = ?,
                    tier = ?,
                    referral_code = ?,
                    referred_by = ?,
                    referral_count = ?,
                    referral_credited = ?,
                    welcome_sent = ?,
                    coaching_pitch_sent = ?,
                    trial_questions_used = ?,
                    subscription_expiry = ?,
                    total_queries = ?,
                    email = ?,
                    google_id = ?,
                    avatar_url = ?,
                    contact_phone = ?,
                    created_at = ?,
                    last_active = ?
                WHERE phone_number = ?
            """, (
                merged_name,
                tier,
                merged_ref_code,
                merged_referred_by,
                merged_ref_count,
                merged_ref_credited,
                merged_welcome,
                merged_pitch,
                merged_trial,
                _db_ts(db, merged_expiry) if merged_expiry else None,
                merged_queries,
                merged_email,
                merged_google_id,
                merged_avatar,
                canon,
                _db_ts(db, merged_created) if merged_created else None,
                _db_ts(db, merged_active) if merged_active else None,
                canon,
            ))
            summary["merged"].append({"canonical": canon, "sources": old_keys})

        # Normalize contact_phone on synthetic-ID users
        for srow in synthetic_rows:
            cp = srow.get("contact_phone")
            if cp:
                cp_norm = normalize_pk_phone_or_none(cp)
                if cp_norm and cp_norm != cp:
                    db.execute(
                        "UPDATE users SET contact_phone = ? WHERE phone_number = ?",
                        (cp_norm, srow["phone_number"]),
                    )
                    summary["contact_phones_updated"] += 1

        # Normalize any remaining orphan phone numbers in child tables
        for child_tbl in ("payment_requests", "coaching_leads", "chat_history"):
            db.execute(f"SELECT DISTINCT phone_number FROM {child_tbl}")
            for crow in db.fetchall():
                raw_p = crow["phone_number"]
                if raw_p and not is_synthetic_user_id(raw_p):
                    canon = normalize_pk_phone_or_none(raw_p)
                    if canon and canon != raw_p:
                        db.execute(
                            f"UPDATE {child_tbl} SET phone_number = ? WHERE phone_number = ?",
                            (canon, raw_p),
                        )
                        summary["child_rows_updated"] += max(0, db.cursor.rowcount or 0)

    return summary
