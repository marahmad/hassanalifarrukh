"""
Comprehensive unit & integration test suite for:
  1. Canonical Pakistani mobile number normalization (923XXXXXXXXX)
  2. Database migration & duplicate row merging (preserving Pro tier, max expiry, sums, child rows)
  3. Verified Google Sign-In Only (One-Tap JWT verification + OAuth 2.0 CSRF state & referral flow)
  4. Signed HttpOnly user_session cookies (/api/auth/me, /api/auth/logout, ignoring spoofed `sender`)
  5. Unique Pakistani phone linking on payment / coaching (blocking claiming another user's number)
  6. Admin security fixes (/webhook `or`->`and` bug, /api/chat `sender==ADMIN_PHONE` spoof blocked, /api/admin/activate)
  7. WhatsApp bridge `X-Bridge-Secret` enforcement
"""
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from fastapi.testclient import TestClient

import src.config as config
import src.database as database
import src.server as server
import src.user_auth as user_auth
from src.phone_utils import (
    format_pk_phone_display,
    normalize_pk_phone,
)


class PhoneNormalizationTests(unittest.TestCase):
    def test_all_pakistani_formats_map_to_canonical(self):
        variants = [
            "03007560313",
            "+923007560313",
            "923007560313",
            "+9203007560313",
            "9203007560313",
            "00923007560313",
            "009203007560313",
            "3007560313",
            "+92 300-756 0313",
            "(0300) 756-0313",
            "  +92 (300) 756.0313 ",
            "\u200e0300-7560313\u200f",
        ]
        for raw in variants:
            ok, canon, err = normalize_pk_phone(raw)
            self.assertTrue(ok, f"Expected valid for {raw!r}, got error: {err}")
            self.assertEqual(canon, "923007560313", f"Failed canonicalization for {raw!r}")

    def test_display_format(self):
        self.assertEqual(format_pk_phone_display("03007560313"), "+92 300 7560313")
        self.assertEqual(format_pk_phone_display("+9203007560313"), "+92 300 7560313")

    def test_invalid_numbers_rejected(self):
        invalid_cases = [
            "",
            "   ",
            "12345",
            "0300756031",
            "030075603139",
            "+922112345678",
            "04235761234",
            "+447911123456",
            "+14155552671",
            "0300abc0313",
            "user@gmail.com",
            "u_1234567890ab",
            "92+3007560313",
        ]
        for raw in invalid_cases:
            ok, canon, err = normalize_pk_phone(raw)
            self.assertFalse(ok, f"Expected invalid for {raw!r}, got {canon!r}")
            self.assertEqual(canon, "")
            self.assertTrue(bool(err))


class AuthAndSecurityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_db = Path(self.tmp_dir.name) / "test_bot.db"

        self._orig_db_url = database.DATABASE_URL
        self._orig_db_path = database.DB_PATH
        database.DATABASE_URL = ""
        database.DB_PATH = self.tmp_db
        database.init_db()

        self._orig_groq = server.groq_client
        server.groq_client = None

        self._orig_bridge_secret = user_auth.WHATSAPP_BRIDGE_SECRET
        user_auth.WHATSAPP_BRIDGE_SECRET = "test_bridge_secret_xyz"

        self._orig_admin_phone = server.ADMIN_PHONE
        server.ADMIN_PHONE = "03009999999"
        self._orig_admin_pw = server.ADMIN_PASSWORD
        self._orig_cfg_admin_pw = config.ADMIN_PASSWORD
        if not server.ADMIN_PASSWORD:
            server.ADMIN_PASSWORD = "test_admin_passkey"
            config.ADMIN_PASSWORD = "test_admin_passkey"

        self._orig_google_id = server.GOOGLE_CLIENT_ID
        self._orig_google_secret = server.GOOGLE_CLIENT_SECRET
        self._orig_auth_google_id = user_auth.GOOGLE_CLIENT_ID
        server.GOOGLE_CLIENT_ID = "test-google-client-id.apps.googleusercontent.com"
        server.GOOGLE_CLIENT_SECRET = "test-google-secret"
        user_auth.GOOGLE_CLIENT_ID = server.GOOGLE_CLIENT_ID

        self.client = TestClient(server.app)

    def tearDown(self):
        database.DATABASE_URL = self._orig_db_url
        database.DB_PATH = self._orig_db_path
        server.groq_client = self._orig_groq
        user_auth.WHATSAPP_BRIDGE_SECRET = self._orig_bridge_secret
        server.ADMIN_PHONE = self._orig_admin_phone
        server.ADMIN_PASSWORD = self._orig_admin_pw
        config.ADMIN_PASSWORD = self._orig_cfg_admin_pw
        server.GOOGLE_CLIENT_ID = self._orig_google_id
        server.GOOGLE_CLIENT_SECRET = self._orig_google_secret
        user_auth.GOOGLE_CLIENT_ID = self._orig_auth_google_id
        self.tmp_dir.cleanup()

    def test_migration_merges_duplicate_phone_formats_preserving_pro_sub(self):
        now = datetime.now()
        exp_later = (now + timedelta(days=25)).isoformat()
        exp_earlier = (now + timedelta(days=5)).isoformat()

        with database.DBContext() as db:
            db.execute("""
                INSERT INTO users (phone_number, name, tier, referral_code, trial_questions_used,
                                   subscription_expiry, total_queries, referral_count, created_at, last_active)
                VALUES (?, 'Hassan Fan', 'pro', 'HASSAN-AA11', 3, ?, 10, 2, ?, ?)
            """, ("03007560313", exp_later, (now - timedelta(days=10)).isoformat(), now.isoformat()))

            db.execute("""
                INSERT INTO users (phone_number, name, tier, referral_code, trial_questions_used,
                                   subscription_expiry, total_queries, referral_count, created_at, last_active)
                VALUES (?, 'User', 'basic', 'HASSAN-BB22', 2, ?, 4, 1, ?, ?)
            """, ("9203007560313", exp_earlier, (now - timedelta(days=2)).isoformat(), now.isoformat()))

            db.execute("""
                INSERT INTO payment_requests (phone_number, trx_id, plan_selected, tier, status, created_at)
                VALUES ('03007560313', 'TRX111', 'pro', 'pro', 'approved', ?)
            """, (now.isoformat(),))
            db.execute("""
                INSERT INTO chat_history (phone_number, role, content, created_at)
                VALUES ('9203007560313', 'user', 'hello', ?)
            """, (now.isoformat(),))

        summary = database.migrate_and_deduplicate_phones()
        self.assertEqual(len(summary["merged"]), 1)
        self.assertEqual(summary["merged"][0]["canonical"], "923007560313")

        with database.DBContext() as db:
            db.execute("SELECT * FROM users")
            rows = [dict(r) for r in db.fetchall()]
            self.assertEqual(len(rows), 1)
            u = rows[0]
            self.assertEqual(u["phone_number"], "923007560313")
            self.assertEqual(u["contact_phone"], "923007560313")
            self.assertEqual(u["tier"], "pro")
            self.assertEqual(u["name"], "Hassan Fan")
            self.assertEqual(u["total_queries"], 14)
            self.assertEqual(u["trial_questions_used"], 5)
            self.assertEqual(u["referral_count"], 3)
            self.assertEqual(u["subscription_expiry"], exp_later)

            db.execute("SELECT phone_number FROM payment_requests")
            self.assertEqual(db.fetchone()["phone_number"], "923007560313")
            db.execute("SELECT phone_number FROM chat_history")
            self.assertEqual(db.fetchone()["phone_number"], "923007560313")

    def test_unauthenticated_chat_rejected_and_sender_spoofing_blocked(self):
        resp = self.client.post("/api/chat", json={"sender": "923007560313", "message": "Hello"})
        self.assertEqual(resp.status_code, 401)

        resp_admin = self.client.post("/api/chat", json={"sender": "923009999999", "message": "/stats"})
        self.assertEqual(resp_admin.status_code, 403)
        resp_act = self.client.post("/api/chat", json={"sender": "03009999999", "message": "/activate 03007560313 pro 30"})
        self.assertEqual(resp_act.status_code, 403)

    def test_google_onetap_session_cookie_phone_linking_and_activation(self):
        fake_claims = {
            "sub": "109876543210987654321",
            "email": "ali.khan@gmail.com",
            "name": "Ali Khan",
            "picture": "https://example.com/ali.jpg",
        }

        with patch.object(server, "verify_google_id_token", return_value=fake_claims):
            r_ok = self.client.post("/api/auth/google", json={"credential": "valid_mocked_google_jwt"})
            self.assertEqual(r_ok.status_code, 200)
            self.assertIn("user_session", r_ok.cookies)
            user_id = r_ok.json()["user"]["id"]

        # /api/auth/me confirms verified Google session
        r_me = self.client.get("/api/auth/me")
        self.assertTrue(r_me.json()["authenticated"])
        self.assertEqual(r_me.json()["user"]["email"], "ali.khan@gmail.com")
        self.assertTrue(r_me.json()["google_enabled"])

        # Chatting uses cookie identity even if caller tries to spoof `sender` of a paid user
        database.activate_user("923007560313", days=30, tier="pro")
        r_chat = self.client.post("/api/chat", json={"sender": "923007560313", "message": "How to build discipline?"})
        self.assertEqual(r_chat.status_code, 200)
        self.assertEqual(r_chat.json()["user"]["id"], user_id)
        self.assertFalse(r_chat.json()["user"]["is_subscribed"])

        # Trying to claim 923007560313 via ANY format is BLOCKED (409)
        for variant in ["03007560313", "+923007560313", "+9203007560313", "00923007560313"]:
            r_steal = self.client.post("/api/auth/link-phone", json={"phone": variant})
            self.assertEqual(r_steal.status_code, 409, f"Should block claiming existing phone {variant}")

        # Submitting TRX without a linked Pakistani phone returns 400 phone_required
        r_trx_no_phone = self.client.post("/api/chat", json={"message": "TRX: 20261122001 PRO"})
        self.assertEqual(r_trx_no_phone.status_code, 400)
        self.assertEqual(r_trx_no_phone.json()["code"], "phone_required")

        # Invalid/non-PK phone rejected (400)
        r_inv = self.client.post("/api/auth/link-phone", json={"phone": "+447911123456"})
        self.assertEqual(r_inv.status_code, 400)

        # Valid unowned Pakistani phone (even in +9203... typo format) normalizes and links
        r_link = self.client.post("/api/auth/link-phone", json={"phone": "+9203211234567"})
        self.assertEqual(r_link.status_code, 200)
        self.assertEqual(r_link.json()["contact_phone"], "923211234567")
        self.assertEqual(r_link.json()["display_phone"], "+92 321 1234567")

        # Submitting TRX now succeeds
        r_trx_ok = self.client.post("/api/chat", json={"message": "TRX: 20261122001 PRO"})
        self.assertEqual(r_trx_ok.status_code, 200)
        self.assertIn("20261122001", r_trx_ok.json()["reply"])

        # Admin activating by `0321-1234567` activates Ali Khan's Google-verified account
        r_adm_login = self.client.post("/api/admin/login", json={"passkey": config.ADMIN_PASSWORD})
        self.assertEqual(r_adm_login.status_code, 200)
        r_act = self.client.post("/api/admin/activate", json={"identifier": "0321-1234567", "tier": "pro", "days": 30})
        self.assertEqual(r_act.status_code, 200)

        r_me2 = self.client.get("/api/auth/me")
        self.assertTrue(r_me2.json()["user"]["is_subscribed"])
        self.assertEqual(r_me2.json()["user"]["tier"], "pro")

        # Admin dashboard displays both formatted PK phone and Google email
        r_dash = self.client.get("/admin/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("+92 321 1234567", r_dash.text)
        self.assertIn("ali.khan@gmail.com", r_dash.text)

        # Logout clears user_session cookie
        self.client.post("/api/auth/logout")
        self.assertFalse(self.client.get("/api/auth/me").json()["authenticated"])

    def test_google_oauth_redirect_csrf_and_referral_flow(self):
        # Create a referrer first
        ref_user = database.get_or_create_google_user("sub_ref", "ref@gmail.com", "Referrer")
        ref_code = ref_user["referral_code"]

        # 1. Start OAuth login with optional referral code
        r_start = self.client.get(f"/api/auth/google/login?ref={ref_code}", follow_redirects=False)
        self.assertEqual(r_start.status_code, 307)
        loc = r_start.headers["location"]
        self.assertTrue(loc.startswith("https://accounts.google.com/o/oauth2/v2/auth?"))
        parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)
        state = parsed_qs["state"][0]

        # 2. Forged state is rejected
        r_forged = self.client.get("/api/auth/google/callback?code=abc&state=forged", follow_redirects=False)
        self.assertEqual(r_forged.status_code, 307)
        self.assertIn("auth_error=invalid_state", r_forged.headers["location"])

        # 3. Valid callback exchanges code, sets HttpOnly session cookie (no JSON in URL!), and applies referral
        fake_token_resp = MagicMock()
        fake_token_resp.status_code = 200
        fake_token_resp.json.return_value = {"id_token": "mock_id_token", "access_token": "mock_access"}
        fake_claims = {"sub": "sub_new", "email": "newuser@gmail.com", "name": "New User", "picture": ""}

        with patch.object(server.requests, "post", return_value=fake_token_resp), \
             patch.object(server, "verify_google_id_token", return_value=fake_claims):
            r_cb = self.client.get(f"/api/auth/google/callback?code=validcode&state={urllib.parse.quote(state)}", follow_redirects=False)
            self.assertEqual(r_cb.status_code, 307)
            self.assertEqual(r_cb.headers["location"], "/?signed_in=google")
            self.assertIn("user_session", r_cb.cookies)

        u = database.get_user_by_identifier("newuser@gmail.com")
        self.assertIsNotNone(u)
        self.assertEqual(u["referred_by"], ref_code)

    def test_google_auth_rejects_unverified_payloads_and_home_has_no_otp_or_fake_login(self):
        # Raw email without verified Google JWT is rejected (401)
        r1 = self.client.post("/api/auth/google", json={"email": "victim@gmail.com", "name": "Victim"})
        self.assertEqual(r1.status_code, 401)

        # Fake unsigned JWT is rejected (401)
        r2 = self.client.post("/api/auth/google", json={"credential": "eyJhbGciOiJub25lIn0.eyJlbWFpbCI6InZpY3RpbUBnbWFpbC5jb20ifQ."})
        self.assertEqual(r2.status_code, 401)

        # Home page renders clean Google-only sign-in modal (no Email OTP, no unverified mobile login, no fake prompt)
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("Continue with Google", r_home.text)
        self.assertNotIn("promptGoogleUserEmail", r_home.text)
        self.assertNotIn("submitOnboarding", r_home.text)
        self.assertNotIn("sendEmailOtp", r_home.text)

    def test_bridge_secret_and_webhook_admin_bug_fixed(self):
        r_bad_bridge = self.client.post(
            "/api/chat",
            headers={"X-Bridge-Secret": "wrong"},
            json={"sender": "03001112222", "message": "Salam"},
        )
        self.assertEqual(r_bad_bridge.status_code, 401)

        r_ok_bridge = self.client.post(
            "/api/chat",
            headers={"X-Bridge-Secret": "test_bridge_secret_xyz"},
            json={"sender": "+9203001112222", "message": "Salam"},
        )
        self.assertEqual(r_ok_bridge.status_code, 200)
        self.assertEqual(r_ok_bridge.json()["user"]["id"], "923001112222")

        r_bridge_non_admin = self.client.post(
            "/api/chat",
            headers={"X-Bridge-Secret": "test_bridge_secret_xyz"},
            json={"sender": "923001112222", "message": "/activate 03001112222 pro 30"},
        )
        self.assertEqual(r_bridge_non_admin.status_code, 403)

        r_bridge_admin = self.client.post(
            "/api/chat",
            headers={"X-Bridge-Secret": "test_bridge_secret_xyz"},
            json={"sender": "+923009999999", "message": "/activate +9203001112222 pro 30"},
        )
        self.assertEqual(r_bridge_admin.status_code, 200)
        self.assertIn("923001112222", r_bridge_admin.json()["reply"])
        self.assertTrue(database.get_user_by_identifier("03001112222")["is_subscribed"])

        sent_wa = []
        with patch.object(server, "send_whatsapp_text", side_effect=lambda to, msg: sent_wa.append((to, msg))):
            webhook_payload = {
                "entry": [{
                    "changes": [{
                        "value": {
                            "messages": [{
                                "from": "923005554444",
                                "type": "text",
                                "text": {"body": "/activate 03005554444 pro 30"},
                            }]
                        }
                    }]
                }]
            }
            rw = self.client.post("/webhook", json=webhook_payload)
            self.assertEqual(rw.status_code, 200)
            self.assertTrue(any("Unauthorized" in m for _, m in sent_wa))
            u_victim = database.get_user_by_identifier("923005554444")
            self.assertTrue(u_victim is None or not u_victim["is_subscribed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
