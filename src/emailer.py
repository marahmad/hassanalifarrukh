"""
Non-blocking User Email Notifications (Zero Extra Dependencies)
===============================================================
Automatically notifies Google-verified users by email when:
  1. Admin gifts them bonus free messages (brings churned leads back to chat!)
  2. Their Full Access monthly pass is activated (instant confirmation + referral code)

Supports EITHER:
  - Resend HTTP API (`RESEND_API_KEY`), OR
  - Standard SMTP (`SMTP_USER` + `SMTP_PASSWORD`, e.g. Gmail App Password)

Always runs on daemon threads with strict timeouts so user/admin API requests never stall.
"""

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html as html_lib
import smtplib
import threading

import requests

import src.config as cfg


def is_email_notifications_enabled() -> bool:
    return bool(cfg.RESEND_API_KEY or (cfg.SMTP_USER and cfg.SMTP_PASSWORD))


def _send_via_resend(to_email: str, subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {cfg.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": cfg.EMAIL_FROM,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
                "text": text_body,
            },
            timeout=10,
        )
        if 200 <= resp.status_code < 300:
            return True, "Sent via Resend"
        return False, f"Resend HTTP {resp.status_code}"
    except Exception as e:
        print(f"[Email Warning] Resend failed for {to_email}: {e}")
        return False, str(e)


def _send_via_smtp(to_email: str, subject: str, html_body: str, text_body: str) -> tuple[bool, str]:
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg.EMAIL_FROM
        msg["To"] = to_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(cfg.SMTP_HOST, cfg.SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(cfg.SMTP_USER, cfg.SMTP_PASSWORD)
            server.sendmail(cfg.SMTP_USER, [to_email], msg.as_string())
        return True, "Sent via SMTP"
    except Exception as e:
        print(f"[Email Warning] SMTP failed for {to_email}: {e}")
        return False, str(e)


def send_user_email(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str,
    sync: bool = False,
) -> tuple[bool, str]:
    clean_to = (to_email or "").strip()
    if not clean_to or "@" not in clean_to or not is_email_notifications_enabled():
        return False, "Email notifications disabled or recipient missing."

    def _deliver():
        if cfg.RESEND_API_KEY:
            return _send_via_resend(clean_to, subject, html_body, text_body)
        return _send_via_smtp(clean_to, subject, html_body, text_body)

    if sync:
        return _deliver()
    threading.Thread(target=_deliver, daemon=True).start()
    return True, "Queued"


def _wrap_card_html(title: str, intro: str, highlight_box: str, cta_label: str, cta_url: str, footer_note: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:24px;background:#0b0b0d;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#ffffff;">
  <div style="max-width:520px;margin:0 auto;background:#141416;border:1px solid #27272a;border-radius:16px;padding:28px;">
    <div style="font-size:12px;font-weight:700;letter-spacing:1px;color:#22c55e;text-transform:uppercase;margin-bottom:8px;">Hassan AI • Unofficial AI Mentor</div>
    <h2 style="margin:0 0 12px 0;font-size:21px;color:#ffffff;">{title}</h2>
    <p style="margin:0 0 18px 0;font-size:14.5px;line-height:1.6;color:#d4d4d8;">{intro}</p>
    <div style="background:#18181b;border:1px solid #22c55e44;border-radius:12px;padding:16px;margin-bottom:22px;font-size:14px;line-height:1.55;color:#f4f4f5;">
      {highlight_box}
    </div>
    <a href="{html_lib.escape(cta_url, quote=True)}" style="display:inline-block;background:#22c55e;color:#052e16;font-weight:700;font-size:14.5px;text-decoration:none;padding:12px 22px;border-radius:10px;">
      {cta_label}
    </a>
    <p style="margin:22px 0 0 0;font-size:12px;color:#71717a;line-height:1.5;">{footer_note}</p>
  </div>
</body>
</html>"""


def notify_user_bonus_email(user: dict, bonus_delta: int, new_total_limit: int) -> bool:
    """Notifies a user via email when positive bonus free messages are gifted to their account."""
    if not user or bonus_delta <= 0 or not is_email_notifications_enabled():
        return False
    email = (user.get("email") or "").strip()
    if not email:
        return False

    name = html_lib.escape((user.get("name") or "Friend").strip())
    chat_url = cfg.APP_BASE_URL or "https://hassanalifarrukh.store"
    subject = f"🎁 +{bonus_delta} Bonus Free Messages Added on Hassan AI!"
    html_body = _wrap_card_html(
        title=f"+{bonus_delta} Bonus Messages Unlocked!",
        intro=f"Assalamu Alaikum {name} — we just added <strong>+{bonus_delta} extra free messages</strong> to your Hassan AI account so you can continue your conversation right where you left off.",
        highlight_box=(
            f"<strong>Bonus Added:</strong> +{bonus_delta} free messages<br>"
            f"<strong>Your New Free Limit:</strong> {new_total_limit} messages total"
        ),
        cta_label="Continue Your Chat →",
        cta_url=chat_url,
        footer_note="Your previous chat history and context are saved automatically when you open the link above.",
    )
    text_body = (
        f"Assalamu Alaikum!\n\n"
        f"We just added +{bonus_delta} bonus free messages to your Hassan AI account (new limit: {new_total_limit} messages).\n\n"
        f"Continue your conversation here: {chat_url}\n"
    )
    ok, _ = send_user_email(email, subject, html_body, text_body)
    return ok


def notify_user_activated_email(user: dict, days: int, expiry_str: str, referral_code: str = "") -> bool:
    """Notifies a user via email the moment their Full Access pass is activated."""
    if not user or not is_email_notifications_enabled():
        return False
    email = (user.get("email") or "").strip()
    if not email:
        return False

    name = html_lib.escape((user.get("name") or "Friend").strip())
    chat_url = cfg.APP_BASE_URL or "https://hassanalifarrukh.store"
    ref_code = html_lib.escape((referral_code or user.get("referral_code") or "").strip())
    ref_line = (
        f"<br><strong>Your Invite Code:</strong> <code>{ref_code}</code> (3 paid friends = 1 month FREE)"
        if ref_code
        else ""
    )
    subject = "👑 Your Hassan AI Full Access Pass is Active!"
    html_body = _wrap_card_html(
        title="You're In — Full Access Active!",
        intro=f"Assalamu Alaikum {name} — your payment has been verified and unlimited chats on Hassan AI are now unlocked.",
        highlight_box=(
            f"<strong>Plan:</strong> Full Access Pass ({days} days)<br>"
            f"<strong>Valid Until:</strong> {html_lib.escape(expiry_str)}{ref_line}"
        ),
        cta_label="Open Hassan AI Now →",
        cta_url=chat_url,
        footer_note="Koi limit nahi — jab dil kare message ya voice note bhej dein.",
    )
    text_body = (
        f"Assalamu Alaikum!\n\n"
        f"Your Hassan AI Full Access Pass ({days} days) is now active until {expiry_str}.\n"
        f"Open chat: {chat_url}\n"
    )
    ok, _ = send_user_email(email, subject, html_body, text_body)
    return ok
