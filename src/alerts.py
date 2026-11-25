"""
Instant non-blocking admin phone alerts via Telegram Bot API.
Sends clean PKT-timestamped notifications for:
  1. New user signups
  2. Users hitting the free-trial paywall
  3. Incoming EasyPaisa / Allied Bank / Raast TRX payment submissions
Never blocks or crashes user requests (runs on daemon threads with strict timeout).
"""

import html as html_lib
import threading
from datetime import datetime, timedelta, timezone

import requests

from src.config import MONTHLY_PRICE_PKR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

PKT_TZ = timezone(timedelta(hours=5))
DASHBOARD_URL = "https://hassanalifarrukh.store/admin/dashboard"


def is_telegram_alerts_enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def _now_pkt_str() -> str:
    now_pkt = datetime.now(timezone.utc).astimezone(PKT_TZ)
    hour12 = now_pkt.hour % 12 or 12
    ampm = "AM" if now_pkt.hour < 12 else "PM"
    return f"{now_pkt.strftime('%b')} {now_pkt.day}, {hour12}:{now_pkt.minute:02d} {ampm} PKT"


def _esc(val: str) -> str:
    return html_lib.escape(str(val or "").strip(), quote=True)


def _post_telegram(html_text: str, reply_markup: dict | None = None) -> tuple[bool, str]:
    if not is_telegram_alerts_enabled():
        return False, "Telegram bot token or chat ID not configured."
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "Alert delivered"
        return False, f"Telegram HTTP {resp.status_code}"
    except Exception as e:
        print(f"[Telegram Alert Warning] Could not send alert: {e}")
        return False, str(e)


def send_telegram_alert(html_text: str, sync: bool = False, reply_markup: dict | None = None) -> tuple[bool, str]:
    """Dispatches a Telegram alert asynchronously by default so requests never block."""
    if not is_telegram_alerts_enabled():
        return False, "Telegram alerts not configured."
    if sync:
        return _post_telegram(html_text, reply_markup=reply_markup)
    t = threading.Thread(target=_post_telegram, args=(html_text, reply_markup), daemon=True)
    t.start()
    return True, "Queued"


def answer_telegram_callback(callback_query_id: str, text: str = "", show_alert: bool = False) -> bool:
    """Acknowledges an inline button tap in Telegram so the loading spinner disappears immediately."""
    if not TELEGRAM_BOT_TOKEN or not callback_query_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={
                "callback_query_id": callback_query_id,
                "text": (text or "")[:190],
                "show_alert": show_alert,
            },
            timeout=8,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram Callback Warning] answerCallbackQuery failed: {e}")
        return False


def edit_telegram_message(chat_id: str, message_id: int, html_text: str) -> bool:
    """Updates an existing Telegram alert message (and clears inline buttons after action)."""
    if not TELEGRAM_BOT_TOKEN or not chat_id or not message_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText",
            json={
                "chat_id": str(chat_id),
                "message_id": int(message_id),
                "text": html_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": []},
            },
            timeout=8,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"[Telegram Edit Warning] editMessageText failed: {e}")
        return False


def notify_new_signup(user: dict, ref_code_used: str = ""):
    if not is_telegram_alerts_enabled() or not user:
        return
    name = _esc(user.get("name") or "User")
    email = _esc(user.get("email") or "")
    phone = _esc(user.get("contact_phone") or "")
    contact = " · ".join(p for p in (email, phone) if p) or _esc(user.get("phone_number") or "")
    ref_line = f"\n🎁 <b>Referred by:</b> <code>{_esc(ref_code_used)}</code>" if ref_code_used else ""
    msg = (
        f"🆕 <b>New Hassan AI Signup!</b>\n\n"
        f"👤 <b>Name:</b> {name}\n"
        f"📧 <b>Account:</b> {contact}{ref_line}\n"
        f"🕒 <b>Time:</b> {_now_pkt_str()}\n\n"
        f"🔗 <a href=\"{DASHBOARD_URL}\">Open Admin Dashboard</a>"
    )
    send_telegram_alert(msg)


def notify_paywall_hit(user: dict, last_question: str = ""):
    if not is_telegram_alerts_enabled() or not user:
        return
    name = _esc(user.get("name") or "User")
    email = _esc(user.get("email") or user.get("contact_phone") or user.get("phone_number") or "")
    used = int(user.get("trial_questions_used") or 0)
    snippet = _esc((last_question or "")[:160])
    q_line = f"\n💬 <b>Last msg:</b> <i>\"{snippet}\"</i>" if snippet else ""
    msg = (
        f"🔥 <b>Hot Lead Hit Paywall!</b>\n\n"
        f"👤 <b>User:</b> {name} ({email})\n"
        f"📊 <b>Questions Used:</b> {used}{q_line}\n"
        f"🕒 <b>Time:</b> {_now_pkt_str()}\n\n"
        f"💡 Tip: Follow up or gift bonus messages from <a href=\"{DASHBOARD_URL}#conversations\">Dashboard Chats</a>"
    )
    send_telegram_alert(msg)


def notify_payment_submitted(user: dict, trx_id: str, ref_code: str = "", grace_granted: bool = False):
    if not is_telegram_alerts_enabled() or not user:
        return
    name = _esc(user.get("name") or "User")
    email = _esc(user.get("email") or "")
    phone = _esc(user.get("contact_phone") or user.get("phone_number") or "")
    contact = " · ".join(p for p in (phone, email) if p)
    ref_line = f"\n🎁 <b>Referral:</b> <code>{_esc(ref_code)}</code>" if ref_code else ""
    grace_line = "\n⚡ <i>User unlocked +2 temporary grace messages while waiting.</i>" if grace_granted else ""
    safe_cb_trx = str(trx_id or "").strip()[:48]
    reply_markup = (
        {
            "inline_keyboard": [
                [
                    {"text": "✅ Approve 30 Days", "callback_data": f"approve:{safe_cb_trx}"},
                    {"text": "❌ Reject TRX", "callback_data": f"reject:{safe_cb_trx}"},
                ]
            ]
        }
        if safe_cb_trx
        else None
    )
    msg = (
        f"💰 <b>New Payment Submitted (PKR {MONTHLY_PRICE_PKR:,})!</b>\n\n"
        f"👤 <b>User:</b> {name}\n"
        f"📱 <b>Contact:</b> {contact}\n"
        f"🧾 <b>TRX ID:</b> <code>{_esc(trx_id)}</code>{ref_line}{grace_line}\n"
        f"🕒 <b>Time:</b> {_now_pkt_str()}\n\n"
        f"✅ Tap a button below or open <a href=\"{DASHBOARD_URL}#payments\">Admin Payments</a>"
    )
    send_telegram_alert(msg, reply_markup=reply_markup)
