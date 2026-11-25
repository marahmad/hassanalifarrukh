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
        server._GUEST_PREVIEW_HITS.clear()
        server._TRANSCRIBE_HITS.clear()

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

        # /api/auth/me confirms verified Google session & GET / hydrates auth server-side
        r_me = self.client.get("/api/auth/me")
        self.assertTrue(r_me.json()["authenticated"])
        self.assertEqual(r_me.json()["user"]["email"], "ali.khan@gmail.com")
        self.assertTrue(r_me.json()["google_enabled"])

        r_home_authed = self.client.get("/")
        self.assertEqual(r_home_authed.status_code, 200)
        self.assertIn('"authenticated":true', r_home_authed.text)
        self.assertIn('class="modal-overlay" id="onboarding-modal-overlay"', r_home_authed.text)

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

        # Home page renders live guest-preview chat + Google sign-in modal & unified PKR 1,000 Full Access plan
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("Continue with Google", r_home.text)
        self.assertIn("Watch Real Demo", r_home.text)
        self.assertIn("Opened in TikTok / Instagram?", r_home.text)
        self.assertIn('id="onboarding-modal-overlay"', r_home.text)
        self.assertIn('id="mic-record-btn"', r_home.text)
        self.assertIn('"guest_preview_used":false', r_home.text)
        self.assertIn('media="print" onload="this.media=\'all\'"', r_home.text)
        self.assertNotIn('<script src="https://accounts.google.com/gsi/client" async defer></script>', r_home.text)
        self.assertIn("PKR 1,000", r_home.text)
        self.assertIn("Full Access", r_home.text)
        self.assertNotIn("Starting PKR 500", r_home.text)
        self.assertNotIn("Basic Tier", r_home.text)
        self.assertNotIn("1-on-1", r_home.text)
        self.assertNotIn("verified-badge", r_home.text)
        self.assertNotIn("Official AI", r_home.text)
        self.assertNotIn("promptGoogleUserEmail", r_home.text)
        self.assertNotIn("submitOnboarding", r_home.text)
        self.assertNotIn("sendEmailOtp", r_home.text)

        # Favicon returns 200 SVG (zero iOS 404s) and static allowlist blocks arbitrary files
        r_fav = self.client.get("/favicon.ico")
        self.assertEqual(r_fav.status_code, 200)
        self.assertIn("image/svg+xml", r_fav.headers.get("content-type", ""))
        self.assertEqual(self.client.get("/static/..%2F.env").status_code, 404)
        self.assertEqual(self.client.get("/static/secret.txt").status_code, 404)

    def test_single_1000_pkr_plan_dashboard_mrr_and_paywall(self):
        # Activate 2 users (one via API without tier arg, one via DB helper) -> MRR must be 2 * 1000 = 2000
        r_adm_login = self.client.post("/api/admin/login", json={"passkey": config.ADMIN_PASSWORD})
        self.assertEqual(r_adm_login.status_code, 200)

        r_act1 = self.client.post("/api/admin/activate", json={"identifier": "03001234567", "days": 30})
        self.assertEqual(r_act1.status_code, 200)
        self.assertEqual(r_act1.json()["tier"], "pro")

        database.activate_user("03219876543", days=30)

        metrics = database.get_system_metrics()
        self.assertEqual(metrics["monthly_price_pkr"], 1000)
        self.assertEqual(metrics["active_subscribers"], 2)
        self.assertEqual(metrics["estimated_mrr_pkr"], 2000)

        # Dashboard shows unified PKR 1,000 plan, PKR 2,000 MRR, and no Basic/Pro tier selector or 1-on-1 leads
        r_dash = self.client.get("/admin/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("PKR 1,000", r_dash.text)
        self.assertIn("PKR 2,000", r_dash.text)
        self.assertIn("FULL ACCESS", r_dash.text)
        self.assertNotIn("modal-tier", r_dash.text)
        self.assertNotIn("BASIC (PKR", r_dash.text)
        self.assertNotIn("1-on-1 Coaching Leads", r_dash.text)

        # Paywall text shows PKR 1,000 single plan with no drops/tiers
        self.assertIn("PKR 1,000/month", server.PAYWALL_TEXT)
        self.assertNotIn("BASIC", server.PAYWALL_TEXT)
        self.assertNotIn("drops", server.PAYWALL_TEXT)

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

    def test_generate_ai_reply_uses_reasoning_effort_and_retries_on_length(self):
        from types import SimpleNamespace

        calls = []

        def fake_create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            finish_reason="length",
                            message=SimpleNamespace(content="Truncated mid senten"),
                        )
                    ]
                )
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="Complete reply. Samajh aayi baat?"),
                    )
                ]
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
        with patch.object(server, "groq_client", fake_client):
            reply = server._generate_ai_reply([{"role": "user", "content": "test"}], "free_trial")

        self.assertEqual(reply, "Complete reply. Samajh aayi baat?")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["max_completion_tokens"], 750)
        self.assertEqual(calls[0]["reasoning_effort"], "low")
        self.assertEqual(calls[1]["max_completion_tokens"], 1350)

    def test_generate_ai_reply_fails_over_on_429_rate_limit(self):
        from types import SimpleNamespace

        models_tried = []

        def fake_create(**kwargs):
            models_tried.append(kwargs["model"])
            if kwargs["model"] == "openai/gpt-oss-120b":
                raise RuntimeError("429 rate_limit_exceeded TPM")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content="Failover reply worked cleanly."),
                    )
                ]
            )

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
        )
        with patch.object(server, "groq_client", fake_client):
            reply = server._generate_ai_reply([{"role": "user", "content": "hi"}], "free_trial")

        self.assertEqual(reply, "Failover reply worked cleanly.")
        self.assertEqual(models_tried, ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])

    def test_admin_conversations_endpoint_and_dashboard_viewer(self):
        # Unauthenticated request must be rejected
        r_unauth = self.client.get("/api/admin/conversations")
        self.assertEqual(r_unauth.status_code, 401)

        # Save a conversation including a script-closing payload to verify XSS escaping
        u = database.get_or_create_google_user("g_chat_1", "reader@example.com", "Test Reader")
        database.complete_chat_turn(
            u["phone_number"],
            "Hello </script><script>alert(1)</script>",
            "Hey Test! Good to hear from you.",
        )

        # Login as admin
        r_login = self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD})
        self.assertEqual(r_login.status_code, 200)

        r_convs = self.client.get("/api/admin/conversations")
        self.assertEqual(r_convs.status_code, 200)
        body = r_convs.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["conversations"]), 1)
        conv = body["conversations"][0]
        self.assertEqual(conv["email"], "reader@example.com")
        self.assertEqual(conv["message_count"], 2)
        self.assertEqual(conv["messages"][0]["role"], "user")
        self.assertEqual(conv["messages"][1]["role"], "assistant")

        # Dashboard renders Conversations tab and safely escapes </script>
        r_dash = self.client.get("/admin/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn('id="tab-conversations"', r_dash.text)
        self.assertIn("CONVERSATIONS_DATA =", r_dash.text)
        self.assertIn("submitModalDeleteUser()", r_dash.text)
        self.assertNotIn("</script><script>alert(1)</script>", r_dash.text)

    def test_admin_delete_user_wipes_all_tables(self):
        # Unauthenticated delete must be rejected
        r_unauth = self.client.post("/api/admin/delete-user", json={"identifier": "victim@example.com"})
        self.assertEqual(r_unauth.status_code, 401)

        # Create a full user with linked phone, chat history, payment request, and coaching lead
        u = database.get_or_create_google_user("g_del_1", "victim@example.com", "Delete Me")
        uid = u["phone_number"]
        ok_link, canon, _ = database.link_phone_to_user(uid, "03004445555")
        self.assertTrue(ok_link)
        database.complete_chat_turn(uid, "Hi there", "Hey Delete!")
        database.submit_trx_id(uid, "TRX-DEL-999")
        database.record_coaching_lead(uid)

        # Verify data exists across all tables before deletion
        self.assertIsNotNone(database.get_user_by_identifier("victim@example.com"))
        self.assertEqual(len(database.get_recent_chat_history(uid, limit=10)), 2)

        # Login as admin and delete user via linked phone format
        r_login = self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD})
        self.assertEqual(r_login.status_code, 200)

        r_del = self.client.post("/api/admin/delete-user", json={"identifier": "03004445555"})
        self.assertEqual(r_del.status_code, 200)
        body = r_del.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["deleted"]["user_id"], uid)
        self.assertEqual(body["deleted"]["deleted_counts"]["users"], 1)
        self.assertEqual(body["deleted"]["deleted_counts"]["chat_history"], 2)
        self.assertEqual(body["deleted"]["deleted_counts"]["payment_requests"], 1)
        self.assertEqual(body["deleted"]["deleted_counts"]["coaching_leads"], 1)

        # Confirm user and all child records are completely gone
        self.assertIsNone(database.get_user_by_identifier("victim@example.com"))
        self.assertIsNone(database.get_user_by_identifier("03004445555"))
        self.assertIsNone(database.get_user_by_identifier(uid))
        with database.DBContext() as db:
            for tbl in ("users", "chat_history", "payment_requests", "coaching_leads"):
                db.execute(f"SELECT COUNT(*) AS c FROM {tbl} WHERE phone_number IN (?, ?)", (uid, canon))
                self.assertEqual(db.fetchone()["c"], 0, f"Leftover rows in {tbl}")

        # Deleting again returns 404
        r_again = self.client.post("/api/admin/delete-user", json={"identifier": "victim@example.com"})
        self.assertEqual(r_again.status_code, 404)

    def test_groq_error_returns_503_without_burning_trial_question(self):
        u = database.get_or_create_google_user("g_err_1", "erruser@example.com", "Err User")
        uid = u["phone_number"]

        # Set signed user session cookie
        token = user_auth.generate_user_session_token(uid)
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, token)

        def boom(**kwargs):
            raise RuntimeError("Groq 429 rate limit")

        from types import SimpleNamespace
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=boom))
        )
        with patch.object(server, "groq_client", fake_client), \
             patch.object(server, "search_knowledge", return_value=[]):
            r = self.client.post("/api/chat", json={"message": "Salam bhai"})

        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["status"], "error")
        # Trial question count must STILL be 0!
        u_after = database.get_user_by_identifier(uid)
        self.assertEqual(u_after["trial_questions_used"], 0)
        self.assertEqual(u_after["total_queries"], 0)

    def test_knowledge_chunk_cleaner_filters_noise_and_keeps_real_speech(self):
        from src.knowledge_base import clean_and_validate_chunk

        # Keeps genuine Urdu speech & strips Whisper tail
        ok, cleaned, _ = clean_and_validate_chunk(
            "Caption: Kindness\nSpoken Transcript: آپ نے kindness and generosity پہ کبھی give up نہیں کرنا، ہمیشہ محنت کرو۔ Thank you for watching!",
            "speech",
        )
        self.assertTrue(ok)
        self.assertNotIn("Thank you for watching", cleaned)

        # Rejects Devanagari/Gurmukhi songs, Chinese hallucinations, and music-only clips
        for junk in [
            "Caption: \nSpoken Transcript: जिसे ढूँदता हूं मैं हर कहीं जो कभी मिली मुझे है नहीं मुझे",
            "Caption: GOAT\nSpoken Transcript: ਰਾਤਾਂ ਜਾਗ ਜਾਗ ਕੇ ਮੇਨ ਤਾਂ ਆਂ ਕੀ ਤੀਆ ਕਰਾ ਸਦੀ ਗਾਲ",
            "Caption: \nSpoken Transcript: 请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
            "Caption: \nSpoken Transcript: 🎵",
            "Speech: [Visual reel without audio track]",
        ]:
            ok_j, _, _ = clean_and_validate_chunk(junk, "speech")
            self.assertFalse(ok_j, f"Expected junk chunk to be rejected: {junk[:60]}")

    def test_five_free_trial_and_instant_signup_referral_bonus_and_wa_buttons(self):
        # Verify default free trial is 5 and UI has WhatsApp screenshot + share buttons
        self.assertEqual(config.FREE_TRIAL_QUESTIONS, 5)
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("openWhatsAppPaymentProof()", r_home.text)
        self.assertIn("shareReferralOnWhatsApp()", r_home.text)
        self.assertIn("Send Screenshot on WhatsApp", r_home.text)

        # Create referrer and exhaust all 5 free trial messages -> paywalled
        ref_user = database.get_or_create_google_user("g_ref_owner", "owner@example.com", "Owner")
        ref_uid = ref_user["phone_number"]
        ref_code = ref_user["referral_code"]
        for _ in range(5):
            database.record_user_query(ref_uid)
        can_before, status_before, _ = database.can_user_chat(ref_uid)
        self.assertFalse(can_before)
        self.assertEqual(status_before, "paywall_required")

        # Friend signs up and applies referrer's code -> referrer instantly unlocks +3 bonus messages (cap 8)
        friend = database.get_or_create_google_user("g_ref_friend1", "friend1@example.com", "Friend One")
        ok, msg = database.apply_referral_code(friend["phone_number"], ref_code)
        self.assertTrue(ok)
        self.assertIn("+3 bonus free messages", msg)

        ref_after = database.get_user_by_identifier(ref_uid)
        self.assertEqual(ref_after["bonus_trial_questions"], 3)
        self.assertEqual(database.get_user_trial_limit(ref_after), 8)
        can_after, status_after, _ = database.can_user_chat(ref_uid)
        self.assertTrue(can_after)
        self.assertEqual(status_after, "free_trial")

        # Friend cannot double-apply to farm bonus messages
        ok_dup, _ = database.apply_referral_code(friend["phone_number"], ref_code)
        self.assertFalse(ok_dup)
        self.assertEqual(database.get_user_by_identifier(ref_uid)["bonus_trial_questions"], 3)

        # Bonus caps cleanly at MAX_REFERRAL_BONUS_QUESTIONS (15)
        for i in range(2, 8):
            f_extra = database.get_or_create_google_user(f"g_ref_f{i}", f"friend{i}@example.com", f"Friend {i}")
            database.apply_referral_code(f_extra["phone_number"], ref_code)
        self.assertEqual(
            database.get_user_by_identifier(ref_uid)["bonus_trial_questions"],
            config.MAX_REFERRAL_BONUS_QUESTIONS,
        )

    def test_dashboard_and_conversations_utc_to_pkt_timings(self):
        from datetime import timezone
        from src.dashboard import PKT_TZ, _format_pkt_date, _format_pkt_datetime, render_dashboard_html

        # 1. Verify exact UTC -> PKT (+5h) formatting including midnight rollover
        ref_pkt = datetime(2026, 11, 24, 15, 15, 0, tzinfo=PKT_TZ)
        self.assertEqual(_format_pkt_datetime("2026-11-24T07:09:47.741912Z", ref_pkt), "Today, 12:09 PM")
        self.assertEqual(_format_pkt_datetime("2026-11-24T10:08:54.326730", ref_pkt), "Today, 3:08 PM")
        self.assertEqual(_format_pkt_datetime("2026-11-23T20:30:00Z", ref_pkt), "Today, 1:30 AM")
        self.assertEqual(_format_pkt_datetime("2026-11-23T17:16:31.080626Z", ref_pkt), "Yesterday, 10:16 PM")
        self.assertEqual(_format_pkt_datetime("2026-11-22T13:56:21Z", ref_pkt), "Nov 22, 6:56 PM")
        self.assertEqual(_format_pkt_date("2026-11-26T10:13:04Z"), "Nov 26, 2026")
        self.assertEqual(_format_pkt_date("2026-11-26T21:00:00Z"), "Nov 27, 2026")

        # 2. Verify DB writes UTC and API serializes explicit 'Z' suffix on conversations & users
        u = database.get_or_create_google_user("g_tz_1", "tzuser@example.com", "TZ Tester")
        uid = u["phone_number"]
        self.assertTrue(u["created_at"].endswith("Z"))
        self.assertTrue(u["last_active"].endswith("Z"))
        database.complete_chat_turn(uid, "Ping", "Pong")
        database.submit_trx_id(uid, "TRX-PKT-01")

        convs = database.get_chat_conversations()
        self.assertEqual(len(convs), 1)
        self.assertTrue(convs[0]["last_message_at"].endswith("Z"))
        for msg in convs[0]["messages"]:
            self.assertTrue(msg["created_at"].endswith("Z"))

        # 3. Verify 14-day chart buckets a 00:30 AM PKT message (= previous UTC day 19:30) under "Today"
        now_pkt = datetime.now(timezone.utc).astimezone(PKT_TZ)
        early_pkt_today = datetime(now_pkt.year, now_pkt.month, now_pkt.day, 0, 30, 0, tzinfo=PKT_TZ)
        early_utc = early_pkt_today.astimezone(timezone.utc).replace(tzinfo=None)
        with database.DBContext() as db:
            db.execute(
                "INSERT INTO chat_history (phone_number, role, content, created_at) VALUES (?, 'assistant', 'Early PKT reply', ?)",
                (uid, database._db_ts(db, early_utc)),
            )
        metrics = database.get_system_metrics()
        self.assertEqual(metrics["chart_dates"][-1], "Today")
        self.assertGreaterEqual(metrics["chart_queries"][-1], 2)
        self.assertTrue(metrics["recent_payments"][0]["created_at"].endswith("Z"))

        # 4. Rendered dashboard HTML contains Asia/Karachi PKT formatter and Today, ... time
        html = render_dashboard_html(metrics, convs)
        self.assertIn("const PKT_TZ = 'Asia/Karachi';", html)
        self.assertIn("PKT (UTC+5)", html)
        self.assertIn("Today, ", html)

    def test_deep_20_message_memory_window_and_persona_language_lock(self):
        from src.persona import _detect_query_language, build_hassan_chat_messages

        # 1. Language detection accuracy on real user messages (including ambiguous English "main")
        for en_msg in [
            "Hi",
            "I've good communication skills",
            "Ok great great but I'm a bit reluctant may be afraid of failure",
            "But look i want someone to invest in me as a wife who provide me a house",
            "Not for anyone",
            "Moving alone",
            "My main goal is to stay disciplined",
        ]:
            self.assertEqual(_detect_query_language(en_msg), "english", f"Expected english for: {en_msg}")

        for ur_msg in [
            "Bohot zyada overthinking hoti hai aaj kal",
            "To phr agr mujhe khud e independent bnana hai to phr kaahy ki ser dardi",
            "Bhai yaar me ACCA kar raha hu online",
            "کامیابی کے لیے کیا کروں؟",
        ]:
            self.assertIn(_detect_query_language(ur_msg), ("roman_urdu", "urdu"), f"Expected urdu for: {ur_msg}")

        # 2. 20-message memory window preserves early context (turn 1 still present on turn 10)
        history = []
        for i in range(1, 11):
            history.append({"role": "user", "content": f"Turn {i}: I live in a small city with my mother"})
            history.append({"role": "assistant", "content": f"Reply {i}: Understood, let us work with that."})

        msgs_en = build_hassan_chat_messages(
            "Moving alone",
            retrieved_context=["Spoken Transcript: آپ نے کبھی ہار نہیں ماننی"],
            tier="pro",
            history=history,
            user_name="Royal Deity",
        )
        # 1 system + 20 history + 1 current user = 22 messages
        self.assertEqual(len(msgs_en), 22)
        self.assertIn("Turn 1: I live in a small city with my mother", msgs_en[1]["content"])
        sys_prompt_en = msgs_en[0]["content"]
        self.assertIn("The user's latest message is in ENGLISH", sys_prompt_en)
        # Language lock must appear AFTER the Urdu knowledge archive
        self.assertGreater(
            sys_prompt_en.index("The user's latest message is in ENGLISH"),
            sys_prompt_en.index("RELEVANT KNOWLEDGE ARCHIVE"),
        )

        # 3. Roman Urdu lock enforces respectful tum/aap and bans tu/teri
        msgs_ur = build_hassan_chat_messages("Mujhe samajh nahi aa raha kya karun", tier="trial", history=[])
        self.assertIn("STRICT PRONOUN RULE", msgs_ur[0]["content"])
        self.assertIn("NEVER use rough `tu`", msgs_ur[0]["content"])

        # 4. Markdown sanitizer strips accidental **bold** and ### headers
        self.assertEqual(
            server._sanitize_ai_reply("### Plan\n**Step one**: stay consistent.\n\n\nKeep going."),
            "Plan\nStep one: stay consistent.\n\nKeep going.",
        )

    def test_persistent_chat_history_hydration_and_api_endpoint(self):
        # Unauthenticated history request must be rejected
        self.assertEqual(self.client.get("/api/chat/history").status_code, 401)

        u = database.get_or_create_google_user("g_hist_1", "returning@example.com", "Returning User")
        uid = u["phone_number"]
        database.complete_chat_turn(uid, "First msg </script><script>alert(1)</script>", "First reply")
        database.complete_chat_turn(uid, "Second msg", "Second reply")

        token = user_auth.generate_user_session_token(uid)
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, token)

        r_hist = self.client.get("/api/chat/history")
        self.assertEqual(r_hist.status_code, 200)
        body = r_hist.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(len(body["messages"]), 4)
        self.assertEqual(body["messages"][0]["role"], "user")
        self.assertEqual(body["messages"][3]["content"], "Second reply")
        self.assertTrue(body["messages"][3]["created_at"].endswith("Z"))

        # GET / hydrates INITIAL_HISTORY inline and escapes </script>
        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("const INITIAL_HISTORY =", r_home.text)
        self.assertIn("Second reply", r_home.text)
        self.assertNotIn("</script><script>alert(1)</script>", r_home.text)
        self.assertIn("const PKT_TZ = 'Asia/Karachi';", r_home.text)
        self.assertIn("267+ Public Reels", r_home.text)
        self.assertNotIn("1,011 Knowledge Chunks Active", r_home.text)

    def test_admin_bonus_messages_endpoint_and_dashboard_controls(self):
        # Unauthenticated bonus call rejected
        r_unauth = self.client.post("/api/admin/bonus", json={"identifier": "bonus@example.com", "bonus": 5})
        self.assertEqual(r_unauth.status_code, 401)

        u = database.get_or_create_google_user("g_bonus_1", "bonus@example.com", "Bonus Tester")
        uid = u["phone_number"]

        r_login = self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD})
        self.assertEqual(r_login.status_code, 200)

        # Grant +6 bonus messages -> cap becomes 5 + 6 = 11
        r_add = self.client.post("/api/admin/bonus", json={"identifier": "bonus@example.com", "bonus": 6})
        self.assertEqual(r_add.status_code, 200)
        self.assertEqual(r_add.json()["bonus_trial_questions"], 6)
        self.assertEqual(r_add.json()["max_free_trial"], config.FREE_TRIAL_QUESTIONS + 6)

        # Dashboard displays +6 bonus pill, search filter, bonus controls, and Hassan avatar
        r_dash = self.client.get("/admin/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("+6 bonus", r_dash.text)
        self.assertIn("submitModalBonusMessages()", r_dash.text)
        self.assertIn("filterUsersTable()", r_dash.text)
        self.assertIn("/static/hassan_avatar.jpg", r_dash.text)
        self.assertIn("/apple-touch-icon.png", r_dash.text)

        # Negative adjustment floors cleanly at 0
        r_sub = self.client.post("/api/admin/bonus", json={"identifier": uid, "bonus": -20})
        self.assertEqual(r_sub.status_code, 200)
        self.assertEqual(r_sub.json()["bonus_trial_questions"], 0)
        self.assertEqual(r_sub.json()["max_free_trial"], config.FREE_TRIAL_QUESTIONS)

        # Invalid bonus values & unknown user rejected cleanly
        self.assertEqual(self.client.post("/api/admin/bonus", json={"identifier": uid, "bonus": 0}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/bonus", json={"identifier": uid, "bonus": 500}).status_code, 400)
        self.assertEqual(self.client.post("/api/admin/bonus", json={"identifier": "nobody@example.com", "bonus": 5}).status_code, 404)

    def test_zero_404_production_endpoints_and_consistent_branding(self):
        r_head = self.client.head("/")
        self.assertEqual(r_head.status_code, 200)

        r_icon = self.client.get("/apple-touch-icon.png")
        self.assertEqual(r_icon.status_code, 200)
        self.assertIn("image/png", r_icon.headers.get("content-type", ""))

        r_icon_pre = self.client.get("/apple-touch-icon-precomposed.png")
        self.assertEqual(r_icon_pre.status_code, 200)
        self.assertIn("image/png", r_icon_pre.headers.get("content-type", ""))

        r_robots = self.client.get("/robots.txt")
        self.assertEqual(r_robots.status_code, 200)
        self.assertIn("Disallow: /admin/", r_robots.text)
        self.assertIn("Disallow: /api/", r_robots.text)

        # Unauthenticated /admin/dashboard shows login gate with consistent avatar & touch icon
        r_gate = self.client.get("/admin/dashboard")
        self.assertEqual(r_gate.status_code, 200)
        self.assertIn("/static/hassan_avatar.jpg", r_gate.text)
        self.assertIn("/apple-touch-icon.png", r_gate.text)

    def test_guest_preview_one_message_limit_and_import_after_signin(self):
        # Admin command blocked on guest preview
        r_adm = self.client.post("/api/chat/guest-preview", json={"message": "/activate 03001234567 30"})
        self.assertEqual(r_adm.status_code, 403)

        # 1st guest preview succeeds and sets signed HttpOnly guest_preview_used cookie
        r1 = self.client.post("/api/chat/guest-preview", json={"message": "How do I build discipline?"})
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.json()["status"], "ok")
        self.assertTrue(r1.json()["guest_preview_used"])
        self.assertIn(user_auth.GUEST_PREVIEW_COOKIE, r1.cookies)

        # After preview is used, GET / activates the onboarding sign-in modal immediately
        r_home2 = self.client.get("/")
        self.assertIn('class="modal-overlay active" id="onboarding-modal-overlay"', r_home2.text)

        # 2nd guest preview is blocked (403 signup_required)
        r2 = self.client.post("/api/chat/guest-preview", json={"message": "Second free question"})
        self.assertEqual(r2.status_code, 403)
        self.assertEqual(r2.json()["code"], "signup_required")

        # After signing in, guest preview turn imports once without consuming a trial credit
        u = database.get_or_create_google_user("g_imp_1", "guestconv@example.com", "Guest Converted")
        uid = u["phone_number"]
        token = user_auth.generate_user_session_token(uid)
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, token)

        r_imp1 = self.client.post(
            "/api/chat/import-preview",
            json={"user_text": "How do I build discipline?", "ai_reply": r1.json()["reply"]},
        )
        self.assertEqual(r_imp1.status_code, 200)
        self.assertTrue(r_imp1.json()["imported"])
        self.assertEqual(len(database.get_recent_chat_history(uid, limit=10)), 2)
        self.assertEqual(database.get_user_by_identifier(uid)["trial_questions_used"], 0)

        # Calling import-preview a second time is a safe idempotent no-op
        r_imp2 = self.client.post(
            "/api/chat/import-preview",
            json={"user_text": "Duplicate", "ai_reply": "Duplicate"},
        )
        self.assertFalse(r_imp2.json()["imported"])
        self.assertEqual(len(database.get_recent_chat_history(uid, limit=10)), 2)

    def test_sse_streaming_chat_and_guest_preview_stream(self):
        import json as _json
        from types import SimpleNamespace

        # Unauthenticated /api/chat/stream rejected
        self.assertEqual(self.client.post("/api/chat/stream", json={"message": "Hi"}).status_code, 401)

        # Guest preview stream works & sets X-Accel-Buffering: no + guest cookie
        r_gs = self.client.post("/api/chat/guest-preview/stream", json={"message": "Quick tip?"})
        self.assertEqual(r_gs.status_code, 200)
        self.assertEqual(r_gs.headers.get("x-accel-buffering"), "no")
        self.assertIn(user_auth.GUEST_PREVIEW_COOKIE, r_gs.cookies)
        self.assertIn('"type": "done"', r_gs.text)

        # Authenticated streaming with mocked Groq deltas
        u = database.get_or_create_google_user("g_stream_1", "streamer@example.com", "Stream User")
        uid = u["phone_number"]
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, user_auth.generate_user_session_token(uid))

        def fake_stream_create(**kwargs):
            for tok in ["Stay ", "**consistent** ", "every day."]:
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=tok))])

        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=fake_stream_create))
        )
        with patch.object(server, "groq_client", fake_client), \
             patch.object(server, "search_knowledge", return_value=[]), \
             patch.object(server, "_schedule_memory_update"):
            r_st = self.client.post("/api/chat/stream", json={"message": "Give me advice"})

        self.assertEqual(r_st.status_code, 200)
        self.assertEqual(r_st.headers.get("x-accel-buffering"), "no")
        events = [
            _json.loads(line[5:].strip())
            for line in r_st.text.splitlines()
            if line.startswith("data:")
        ]
        token_events = [e for e in events if e.get("type") == "token"]
        done_events = [e for e in events if e.get("type") == "done"]
        self.assertEqual(len(token_events), 3)
        self.assertEqual(len(done_events), 1)
        # Final sanitized reply strips **bold** and saves turn to DB
        self.assertIn("Stay consistent every day.", done_events[0]["reply"])
        self.assertNotIn("**consistent**", done_events[0]["reply"])
        self.assertEqual(database.get_user_by_identifier(uid)["trial_questions_used"], 1)

    def test_voice_transcription_endpoint_groq_whisper(self):
        from types import SimpleNamespace

        captured_calls = []

        def fake_transcribe(**kwargs):
            captured_calls.append(kwargs)
            return SimpleNamespace(text="Yar discipline kaise banaun?")

        fake_client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=fake_transcribe))
        )

        # Too-short clip rejected (400)
        with patch.object(server, "groq_client", fake_client):
            r_short = self.client.post("/api/chat/transcribe", content=b"tiny", headers={"Content-Type": "audio/webm"})
            self.assertEqual(r_short.status_code, 400)

            # Valid iOS Safari MP4 clip transcribed with voice.m4a + whisper-large-v3-turbo
            r_ok = self.client.post(
                "/api/chat/transcribe",
                content=b"\x00" * 1024,
                headers={"Content-Type": "audio/mp4"},
            )
            self.assertEqual(r_ok.status_code, 200)
            self.assertEqual(r_ok.json()["text"], "Yar discipline kaise banaun?")
            self.assertEqual(captured_calls[0]["model"], "whisper-large-v3-turbo")
            self.assertEqual(captured_calls[0]["file"][0], "voice.m4a")

            # After guest preview is used, unauthenticated voice transcribe requires sign-in (401)
            self.client.cookies.set(user_auth.GUEST_PREVIEW_COOKIE, user_auth.generate_guest_preview_token())
            r_unauth = self.client.post(
                "/api/chat/transcribe",
                content=b"\x00" * 1024,
                headers={"Content-Type": "audio/webm"},
            )
            self.assertEqual(r_unauth.status_code, 401)

    def test_long_term_user_memory_card_persistence_and_dashboard_display(self):
        from src.persona import build_hassan_chat_messages

        u = database.get_or_create_google_user("g_mem_1", "mem@example.com", "Mem User")
        uid = u["phone_number"]
        summary = "- Female, 31, lives in small city with mother (cannot relocate)\n- Strong communication skills"
        self.assertTrue(database.update_user_memory_summary(uid, summary))

        u_loaded = database.get_user_by_identifier(uid)
        self.assertEqual(u_loaded["memory_summary"], summary)

        msgs = build_hassan_chat_messages("What next?", user_name="Mem User", user_memory=u_loaded["memory_summary"])
        self.assertIn("LONG-TERM MEMORY ABOUT THIS USER", msgs[0]["content"])
        self.assertIn("cannot relocate", msgs[0]["content"])

        database.complete_chat_turn(uid, "Hello", "Hey!")
        convs = database.get_chat_conversations()
        self.assertEqual(convs[0]["memory_summary"], summary)

        r_login = self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD})
        self.assertEqual(r_login.status_code, 200)
        r_dash = self.client.get("/admin/dashboard")
        self.assertIn("Long-Term User Memory Card", r_dash.text)
        self.assertIn("cannot relocate", r_dash.text)

    def test_telegram_alerts_triggers_on_signup_paywall_and_payment(self):
        fake_claims = {
            "sub": "tg_sub_1",
            "email": "alertuser@example.com",
            "name": "Alert User",
            "picture": "",
        }
        with patch.object(server, "verify_google_id_token", return_value=fake_claims), \
             patch.object(server, "notify_new_signup") as mock_signup, \
             patch.object(server, "notify_payment_submitted") as mock_pay, \
             patch.object(server, "notify_paywall_hit") as mock_paywall:
            # First login fires new signup alert
            r1 = self.client.post("/api/auth/google", json={"credential": "tok1"})
            self.assertEqual(r1.status_code, 200)
            self.assertEqual(mock_signup.call_count, 1)

            # Second login does NOT double-fire new signup alert
            r2 = self.client.post("/api/auth/google", json={"credential": "tok2"})
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(mock_signup.call_count, 1)

            uid = r1.json()["user"]["id"]
            # Exhaust first 4 free questions, then 5th triggers paywall alert
            for _ in range(config.FREE_TRIAL_QUESTIONS - 1):
                database.record_user_query(uid)
            r_last = self.client.post("/api/chat", json={"message": "My last free question"})
            self.assertEqual(r_last.status_code, 200)
            self.assertEqual(mock_paywall.call_count, 1)

            # Linking phone + submitting TRX fires payment alert
            self.client.post("/api/auth/link-phone", json={"phone": "03007778888"})
            r_trx = self.client.post("/api/chat", json={"message": "TRX: 9988776655"})
            self.assertEqual(r_trx.status_code, 200)
            self.assertEqual(mock_pay.call_count, 1)

        # Admin test-alert endpoint requires auth
        self.assertEqual(self.client.post("/api/admin/test-alert").status_code, 401)
        self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD})
        with patch.object(server, "is_telegram_alerts_enabled", return_value=True), \
             patch.object(server, "send_telegram_alert", return_value=(True, "Alert delivered")):
            r_ta = self.client.post("/api/admin/test-alert")
            self.assertEqual(r_ta.status_code, 200)

    def test_phase1_persona_handle_filter_sticky_language_and_returning_checkin(self):
        from datetime import timezone
        from src.persona import (
            _compute_returning_gap_note,
            _detect_query_language,
            _extract_natural_first_name,
            build_hassan_chat_messages,
        )

        # 1. Smart handle/name filter prevents awkward "Hey Hero" / "Royal" / "Bhai AR"
        self.assertEqual(_extract_natural_first_name("Hero"), "")
        self.assertEqual(_extract_natural_first_name("Royal Deity"), "")
        self.assertEqual(_extract_natural_first_name("AR DJ"), "")
        self.assertEqual(_extract_natural_first_name("Hello World"), "")
        self.assertEqual(_extract_natural_first_name("Ch Asad"), "Asad")
        self.assertEqual(_extract_natural_first_name("SAFI-ULLAH-KHAN"), "Safi")
        self.assertEqual(_extract_natural_first_name("Ahmad hassan"), "Ahmad")

        # 2. Sticky language detection keeps Roman Urdu on short follow-ups ("Yes", "23", "Ok")
        ur_history = [
            {"role": "user", "content": "Yar discipline nahi ban raha aaj kal"},
            {"role": "assistant", "content": "Batao abhi age aur routine kya hai?"},
        ]
        self.assertEqual(_detect_query_language("Yes", history=ur_history), "roman_urdu")
        self.assertEqual(_detect_query_language("23", history=ur_history), "roman_urdu")
        self.assertEqual(_detect_query_language("Ok", history=ur_history), "roman_urdu")
        # Full explicit English message still switches cleanly to English
        self.assertEqual(
            _detect_query_language("Actually let's switch to English, I want career advice", history=ur_history),
            "english",
        )

        # 3. Returning-user check-in activates after >= 20 hours and stays silent for fresh chats
        old_ts = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat().replace("+00:00", "Z")
        recent_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        self.assertIn("3 days ago", _compute_returning_gap_note([{"role": "user", "content": "Hi", "created_at": old_ts}]))
        self.assertEqual(_compute_returning_gap_note([{"role": "user", "content": "Hi", "created_at": recent_ts}]), "")

        # 4. System prompt includes real Hassan worldview, anti-cliché bans, Umang safety, and omits handle names
        msgs = build_hassan_chat_messages(
            "What is life",
            history=[{"role": "user", "content": "Earlier", "created_at": old_ts}],
            user_name="Hero",
        )
        sys_txt = msgs[0]["content"]
        self.assertNotIn("The person messaging you is named Hero", sys_txt)
        self.assertIn("RETURNING USER CHECK-IN", sys_txt)
        self.assertIn("on a scale of 1-10", sys_txt)
        self.assertIn("Umang (0311-7786264)", sys_txt)

    def test_phase1_rag_query_enrichment_and_whisper_noise_cleanup(self):
        from src.knowledge_base import build_rag_search_query, clean_and_validate_chunk

        # Short follow-up inherits prior user topic for pgvector continuity
        hist = [
            {"role": "user", "content": "I struggle with confidence and fear of being judged in gatherings"},
            {"role": "assistant", "content": "Where does this hit hardest?"},
        ]
        enriched = build_rag_search_query("At office meetings", history=hist)
        self.assertIn("fear of being judged", enriched)
        self.assertIn("At office meetings", enriched)

        # Whisper stutter loops collapse and Hataff/greeting errors normalize cleanly
        ok, cleaned, _ = clean_and_validate_chunk(
            "Caption: Test\nSpoken Transcript: جی ازام علیکم خطف کے گارڈ ہمیشہ تیار رہتے ہیں سکھو سکھو سکھو سکھو سکھو محنت ضروری ہے",
            "speech",
        )
        self.assertTrue(ok)
        self.assertIn("جی السلام علیکم", cleaned)
        self.assertIn("ہتف (Hataff) کے گارڈ", cleaned)
        self.assertNotIn("سکھو سکھو سکھو سکھو", cleaned)

    def test_phase1_trx_false_positives_free_greetings_memory_and_clean_web_bubbles(self):
        import json as _json
        from types import SimpleNamespace

        # 1. is_trx_message rejects ordinary chat answers while accepting real TRX IDs
        for normal_msg in [
            "100000 salary",
            "120000 pkr",
            "2024-2026",
            "22saal",
            "75percent",
            "bs2026",
            "03001234567",
        ]:
            self.assertFalse(server.is_trx_message(normal_msg, "free_trial"), f"False positive TRX on: {normal_msg!r}")

        for real_trx in [
            "TRX: 20261122001 PRO",
            "TRX 107382822$37",
            "202410319874",
            "TID99887766 PRO",
        ]:
            self.assertTrue(server.is_trx_message(real_trx, "free_trial"), f"Missed valid TRX: {real_trx!r}")

        # 2. Pure greeting does NOT burn a free-trial credit on Turn 1; real question does
        u = database.get_or_create_google_user("g_p1_1", "p1user@example.com", "Ch Asad")
        uid = u["phone_number"]
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, user_auth.generate_user_session_token(uid))

        r_greet = self.client.post("/api/chat", json={"message": "Aoa Hassan bhai"})
        self.assertEqual(r_greet.status_code, 200)
        self.assertEqual(database.get_user_by_identifier(uid)["trial_questions_used"], 0)
        self.assertEqual(database.get_user_by_identifier(uid)["total_queries"], 1)
        # Web bubbles must NOT contain ugly raw underscore trial footers
        self.assertNotIn("_(Free Trial:", r_greet.json()["reply"])

        r_real = self.client.post("/api/chat", json={"message": "How do I build real confidence?"})
        self.assertEqual(r_real.status_code, 200)
        self.assertEqual(database.get_user_by_identifier(uid)["trial_questions_used"], 1)
        self.assertNotIn("_(Free Trial:", r_real.json()["reply"])

        # 3. Final free turn on /api/chat/stream sets trial_exhausted=True with zero raw underscores
        for _ in range(config.FREE_TRIAL_QUESTIONS - 2):
            database.record_user_query(uid)

        def fake_stream(**kwargs):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hassan: Stay sharp and consistent."))])

        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_stream)))
        with patch.object(server, "groq_client", fake_client), \
             patch.object(server, "search_knowledge", return_value=[]), \
             patch.object(server, "_schedule_memory_update"), \
             patch.object(server, "notify_paywall_hit") as mock_pw:
            r_last = self.client.post("/api/chat/stream", json={"message": "My final free question"})

        self.assertEqual(r_last.status_code, 200)
        self.assertEqual(mock_pw.call_count, 1)
        events = [_json.loads(l[5:].strip()) for l in r_last.text.splitlines() if l.startswith("data:")]
        done_evt = [e for e in events if e.get("type") == "done"][0]
        self.assertTrue(done_evt["trial_exhausted"])
        self.assertEqual(done_evt["reply"], "Stay sharp and consistent.")
        self.assertNotIn("_(You have used your last free question", done_evt["reply"])

        # 4. Memory model order puts fast non-reasoning model first & refreshes on Turn 1, 2, 4, 7
        self.assertEqual(server.GROQ_MEMORY_MODELS[0][0], "llama-3.1-8b-instant")
        self.assertTrue(server._should_refresh_memory(1))
        self.assertTrue(server._should_refresh_memory(2))
        self.assertFalse(server._should_refresh_memory(3))
        self.assertTrue(server._should_refresh_memory(4))
        self.assertTrue(server._should_refresh_memory(7))

        # 5. Home page renders gender-neutral bilingual chips & italic formatter
        r_home = self.client.get("/")
        self.assertIn("Confidence &amp; fear of judgment", r_home.text)
        self.assertNotIn("nahi bana pa raha", r_home.text)
        self.assertIn("_buildTrialExhaustedCardHtml", r_home.text)

    def test_phase2_pwa_manifest_feedback_and_daily_streak(self):
        from datetime import timezone
        from src.dashboard import PKT_TZ

        # 1. PWA Manifest & Phase 2 UI hooks on GET /
        r_man = self.client.get("/manifest.json")
        self.assertEqual(r_man.status_code, 200)
        self.assertIn("application/manifest+json", r_man.headers.get("content-type", ""))
        man = r_man.json()
        self.assertEqual(man["short_name"], "Hassan AI")
        self.assertEqual(man["display"], "standalone")

        r_home = self.client.get("/")
        self.assertEqual(r_home.status_code, 200)
        for token in (
            'rel="manifest" href="/manifest.json"',
            "openShareCardModal",
            "rateAiReply",
            "triggerDailyCheckIn",
            "streak-badge-pill",
            "quote-card-canvas",
        ):
            self.assertIn(token, r_home.text)

        # 2. Feedback API requires auth, validates rating, aggregates metrics, and cleans up on delete
        self.assertEqual(self.client.post("/api/chat/feedback", json={"rating": "up"}).status_code, 401)

        u = database.get_or_create_google_user("g_p2_fb", "fbuser@example.com", "Feedback User")
        uid = u["phone_number"]
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, user_auth.generate_user_session_token(uid))

        self.assertEqual(self.client.post("/api/chat/feedback", json={"rating": "meh"}).status_code, 400)
        self.assertEqual(
            self.client.post("/api/chat/feedback", json={"rating": "up", "snippet": "Great advice"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/chat/feedback", json={"rating": "up", "snippet": "Solid answer"}).status_code,
            200,
        )
        self.assertEqual(
            self.client.post("/api/chat/feedback", json={"rating": "down", "snippet": "Too short"}).status_code,
            200,
        )

        fb = database.get_feedback_summary()
        self.assertEqual(fb["up"], 2)
        self.assertEqual(fb["down"], 1)
        self.assertEqual(fb["satisfaction_pct"], 66.7)
        metrics = database.get_system_metrics()
        self.assertEqual(metrics["feedback_up"], 2)
        self.assertEqual(metrics["feedback_down"], 1)
        self.assertEqual(metrics["feedback_satisfaction_pct"], 66.7)

        # 3. Daily PKT check-in streak across 3 consecutive days (today, yesterday, 2 days ago)
        now_pkt = datetime.now(timezone.utc).astimezone(PKT_TZ)
        with database.DBContext() as db:
            for offset in (0, 1, 2):
                day_pkt = datetime(now_pkt.year, now_pkt.month, now_pkt.day, 14, 0, 0, tzinfo=PKT_TZ) - timedelta(days=offset)
                day_utc = day_pkt.astimezone(timezone.utc).replace(tzinfo=None)
                db.execute(
                    "INSERT INTO chat_history (phone_number, role, content, created_at) VALUES (?, 'user', ?, ?)",
                    (uid, f"Day -{offset} check-in", database._db_ts(db, day_utc)),
                )

        streak = database.get_user_streak(uid)
        self.assertEqual(streak["current_streak"], 3)
        self.assertEqual(streak["longest_streak"], 3)
        self.assertTrue(streak["checked_in_today"])

        r_me = self.client.get("/api/auth/me")
        self.assertEqual(r_me.status_code, 200)
        self.assertEqual(r_me.json()["user"]["current_streak"], 3)
        self.assertTrue(r_me.json()["user"]["checked_in_today"])

        # Deleting user also clears their chat_feedback rows
        ok_del, _ = database.delete_user_completely(uid)
        self.assertTrue(ok_del)
        self.assertEqual(database.get_feedback_summary()["total"], 0)

    def test_phase2_pending_trx_grace_window_and_telegram_1tap_webhook(self):
        # 1. Paywalled user gets +2 instant grace messages ONCE upon submitting TRX
        u = database.get_or_create_google_user("g_p2_grace", "payer@example.com", "Grace Payer")
        uid = u["phone_number"]
        self.client.cookies.set(user_auth.USER_SESSION_COOKIE, user_auth.generate_user_session_token(uid))
        self.client.post("/api/auth/link-phone", json={"phone": "03005554433"})

        for _ in range(config.FREE_TRIAL_QUESTIONS):
            database.record_user_query(uid)
        can_before, status_before, _ = database.can_user_chat(uid)
        self.assertFalse(can_before)
        self.assertEqual(status_before, "paywall_required")

        with patch.object(server, "notify_payment_submitted") as mock_tg_pay:
            r_trx1 = self.client.post("/api/chat", json={"message": "TRX: 8877665544"})
            self.assertEqual(r_trx1.status_code, 200)
            self.assertTrue(r_trx1.json()["grace_granted"])
            self.assertIn("instant messages", r_trx1.json()["reply"])
            self.assertTrue(mock_tg_pay.call_args.kwargs.get("grace_granted"))

        # User can immediately chat again with their temporary grace credit!
        can_after, _, _ = database.can_user_chat(uid)
        self.assertTrue(can_after)
        self.assertEqual(
            database.get_user_by_identifier(uid)["bonus_trial_questions"],
            config.PENDING_TRX_GRACE_QUESTIONS,
        )

        # Submitting a 2nd TRX must NOT double-grant temporary grace messages
        with patch.object(server, "notify_payment_submitted"):
            r_trx2 = self.client.post("/api/chat", json={"message": "TRX: 8877665599"})
            self.assertEqual(r_trx2.status_code, 200)
            self.assertFalse(r_trx2.json()["grace_granted"])
        self.assertEqual(
            database.get_user_by_identifier(uid)["bonus_trial_questions"],
            config.PENDING_TRX_GRACE_QUESTIONS,
        )

        # Non-paywalled user gets NO extra grace messages when submitting TRX early
        u_early = database.get_or_create_google_user("g_p2_early", "early@example.com", "Early Payer")
        granted_early, _ = database.grant_pending_trx_grace(u_early["phone_number"])
        self.assertFalse(granted_early)

        # 2. Telegram 1-tap webhook security + Reject + Approve flows
        with patch.object(server, "TELEGRAM_WEBHOOK_SECRET", "super-secret-tg"), \
             patch.object(server, "TELEGRAM_CHAT_ID", "123456789"), \
             patch.object(server, "answer_telegram_callback") as mock_ans, \
             patch.object(server, "edit_telegram_message") as mock_edit, \
             patch.object(server, "notify_user_activated_email") as mock_email:
            # Missing / wrong secret -> 403
            r_bad_sec = self.client.post("/api/telegram/webhook", json={})
            self.assertEqual(r_bad_sec.status_code, 403)

            # Wrong chat_id -> 403
            r_bad_chat = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-tg"},
                json={"callback_query": {"id": "cb0", "data": "approve:8877665544", "message": {"message_id": 10, "chat": {"id": 999999}}}},
            )
            self.assertEqual(r_bad_chat.status_code, 403)

            # Reject fake TRX -> marks rejected & revokes temporary grace messages
            r_rej = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-tg"},
                json={"callback_query": {"id": "cb1", "data": "reject:8877665544", "message": {"message_id": 11, "chat": {"id": 123456789}}}},
            )
            self.assertEqual(r_rej.status_code, 200)
            self.assertEqual(r_rej.json()["action"], "rejected")
            self.assertEqual(r_rej.json()["grace_revoked"], config.PENDING_TRX_GRACE_QUESTIONS)
            self.assertEqual(database.get_payment_by_trx("8877665544")["status"], "rejected")
            self.assertEqual(database.get_user_by_identifier(uid)["bonus_trial_questions"], 0)
            self.assertEqual(mock_edit.call_count, 1)

            # Approve real TRX -> activates 30-day Full Access + sends activation email + edits message
            r_app = self.client.post(
                "/api/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "super-secret-tg"},
                json={"callback_query": {"id": "cb2", "data": "approve:8877665599", "message": {"message_id": 12, "chat": {"id": 123456789}}}},
            )
            self.assertEqual(r_app.status_code, 200)
            self.assertEqual(r_app.json()["action"], "approved")
            self.assertTrue(database.get_user_by_identifier(uid)["is_subscribed"])
            self.assertEqual(mock_email.call_count, 1)
            self.assertEqual(mock_edit.call_count, 2)
            self.assertGreaterEqual(mock_ans.call_count, 2)

    def test_phase2_auto_emails_and_admin_ai_insights(self):
        from types import SimpleNamespace

        u = database.get_or_create_google_user("g_p2_mail", "mailuser@example.com", "Mail User")
        uid = u["phone_number"]
        database.complete_chat_turn(uid, "Ghar walay har baat pe criticize karte hain", "Seedhi baat hai boundaries set karo.")

        # Insights requires admin login
        self.assertEqual(self.client.post("/api/admin/insights").status_code, 401)
        self.assertEqual(self.client.post("/api/admin/login", json={"passkey": server.ADMIN_PASSWORD}).status_code, 200)

        # Positive bonus triggers bonus email; negative adjustment does NOT; activate triggers activation email
        with patch.object(server, "notify_user_bonus_email", return_value=True) as mock_bonus_mail, \
             patch.object(server, "notify_user_activated_email", return_value=True) as mock_act_mail:
            r_b1 = self.client.post("/api/admin/bonus", json={"identifier": "mailuser@example.com", "bonus": 5})
            self.assertEqual(r_b1.status_code, 200)
            self.assertTrue(r_b1.json()["email_notified"])
            self.assertEqual(mock_bonus_mail.call_count, 1)

            r_b2 = self.client.post("/api/admin/bonus", json={"identifier": "mailuser@example.com", "bonus": -2})
            self.assertEqual(r_b2.status_code, 200)
            self.assertFalse(r_b2.json()["email_notified"])
            self.assertEqual(mock_bonus_mail.call_count, 1)

            r_act = self.client.post("/api/admin/activate", json={"identifier": "mailuser@example.com", "days": 30})
            self.assertEqual(r_act.status_code, 200)
            self.assertEqual(mock_act_mail.call_count, 1)

        # Admin AI Insights returns structured brief + dashboard renders Insights panel & satisfaction stats
        fake_resp = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="1. TOP 3 USER STRUGGLES:\n- Family pressure"))]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: fake_resp))
        )
        with patch.object(server, "groq_client", fake_client):
            r_ins = self.client.post("/api/admin/insights")
            self.assertEqual(r_ins.status_code, 200)
            body = r_ins.json()
            self.assertEqual(body["status"], "ok")
            self.assertEqual(body["threads_analyzed"], 1)
            self.assertIn("TOP 3 USER STRUGGLES", body["insights"])

        r_dash = self.client.get("/admin/dashboard")
        self.assertEqual(r_dash.status_code, 200)
        self.assertIn("AI Chat Insights", r_dash.text)
        self.assertIn("generateChatInsights()", r_dash.text)
        self.assertIn("Reply Satisfaction", r_dash.text)

    def test_phase2_whatsapp_voice_note_and_bridge_transcribe(self):
        from types import SimpleNamespace

        # 1. Meta WhatsApp Cloud API audio webhook downloads + transcribes + replies
        wa_payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "923001112233",
                            "type": "audio",
                            "audio": {"id": "media_voice_123", "mime_type": "audio/ogg; codecs=opus"},
                        }]
                    }
                }]
            }]
        }
        sent_wa = []
        with patch.object(server, "_download_whatsapp_media", return_value=(b"\x00" * 512, "audio/ogg")) as mock_dl, \
             patch.object(server, "_transcribe_audio_bytes", return_value=("Yar gym consistent kaise rakhun?", None, 200)) as mock_tr, \
             patch.object(server, "send_whatsapp_text", side_effect=lambda to, txt: sent_wa.append((to, txt)) or True), \
             patch.object(server, "search_knowledge", return_value=[]):
            r_wh = self.client.post("/webhook", json=wa_payload)
            self.assertEqual(r_wh.status_code, 200)
            self.assertEqual(mock_dl.call_count, 1)
            self.assertEqual(mock_tr.call_count, 1)
            self.assertEqual(len(sent_wa), 1)
            self.assertEqual(sent_wa[0][0], "923001112233")

        # 2. Baileys bridge can authenticate to /api/chat/transcribe via X-Bridge-Secret
        fake_tr_client = SimpleNamespace(
            audio=SimpleNamespace(
                transcriptions=SimpleNamespace(
                    create=lambda **kw: SimpleNamespace(text="Assalamu Alaikum Hassan bhai")
                )
            )
        )
        self.client.cookies.set(user_auth.GUEST_PREVIEW_COOKIE, user_auth.generate_guest_preview_token())
        with patch.object(server, "groq_client", fake_tr_client):
            r_br_bad = self.client.post(
                "/api/chat/transcribe",
                content=b"\x00" * 600,
                headers={"Content-Type": "audio/ogg", "X-Bridge-Secret": "wrong_secret"},
            )
            self.assertEqual(r_br_bad.status_code, 401)

            r_br = self.client.post(
                "/api/chat/transcribe",
                content=b"\x00" * 600,
                headers={"Content-Type": "audio/ogg", "X-Bridge-Secret": "test_bridge_secret_xyz"},
            )
            self.assertEqual(r_br.status_code, 200)
            self.assertEqual(r_br.json()["text"], "Assalamu Alaikum Hassan bhai")


if __name__ == "__main__":
    unittest.main(verbosity=2)

