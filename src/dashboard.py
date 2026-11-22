"""
Live production management dashboard for Hassan Ali Farrukh AI.
Displays exclusively real database metrics, 14-day database activity telemetry,
user/payment/lead tables, and subscription activation controls (zero mock data).
"""

from datetime import datetime, timedelta
import html as html_lib
import json

from src.config import (
    FREE_TRIAL_QUESTIONS,
    MONTHLY_PRICE_PKR,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_TOKEN,
)
from src.phone_utils import format_pk_phone_display, normalize_pk_phone_or_none

def render_dashboard_html(metrics: dict) -> str:
    def _escape(val: str) -> str:
        return html_lib.escape(str(val or ""), quote=True)

    def _format_user_cell(rec: dict) -> tuple[str, str, str]:
        """Returns (html_cell, primary_identifier_for_activate, canonical_wa_phone)."""
        raw_id = str(rec.get("phone_number") or "")
        canon_phone = normalize_pk_phone_or_none(rec.get("contact_phone")) or normalize_pk_phone_or_none(raw_id) or ""
        email = str(rec.get("email") or "").strip()
        display_phone = format_pk_phone_display(canon_phone) if canon_phone else ""

        if display_phone and email:
            cell = f"{_escape(display_phone)}<div style='font-size:11.5px;color:#71717a;font-weight:400;'>{_escape(email)}</div>"
        elif display_phone:
            cell = _escape(display_phone)
        elif email:
            cell = _escape(email)
        else:
            cell = _escape(raw_id)

        activate_target = canon_phone or email or raw_id
        return cell, activate_target, canon_phone

    # 1. Universal Info Icon Helper
    def info_badge(text: str) -> str:
        return f"""<span class="info-tooltip" tabindex="0" data-tooltip="{text}">
            <svg class="info-icon" viewBox="0 0 16 16" fill="currentColor">
                <path d="M8 15A7 7 0 1 1 8 1a7 7 0 0 1 0 14zm0 1A8 8 0 1 0 8 0a8 8 0 0 0 0 16z"/>
                <path d="m8.93 6.588-2.29.287-.082.38.45.083c.294.07.352.176.288.469l-.738 3.468c-.194.897.105 1.319.808 1.319.545 0 1.178-.252 1.465-.598l.088-.416c-.2.176-.492.246-.686.246-.275 0-.375-.193-.304-.533L8.93 6.588zM9 4.5a1 1 0 1 1-2 0 1 1 0 0 1 2 0z"/>
            </svg>
        </span>"""

    # 2. Table Row Generators
    def build_payments_rows(payments: list) -> str:
        rows = ""
        for p in payments:
            is_approved = p.get("status") == "approved"
            status_color = "#34d399" if is_approved else "#fbbf24"
            status_bg = "rgba(52, 211, 153, 0.1)" if is_approved else "rgba(251, 191, 36, 0.1)"
            user_cell, activate_target, _ = _format_user_cell(p)
            
            action_btn = ""
            if not is_approved:
                action_btn = f"""<button onclick="quickActivate('{_escape(activate_target)}')" class="btn-action">Approve &amp; Activate</button>"""
            else:
                action_btn = "<span style='color: #71717a; font-size: 12px;'>✓ Activated</span>"

            rows += f"""
            <tr>
                <td style="font-weight: 500; color: #f4f4f5;">{user_cell}</td>
                <td><code class="code-pill">{_escape(p['trx_id'])}</code></td>
                <td><span class="plan-pill">PKR {MONTHLY_PRICE_PKR:,} / MO</span></td>
                <td><span class="status-pill" style="color: {status_color}; background: {status_bg};">● {_escape(p['status'].upper())}</span></td>
                <td style="color: #71717a; font-size: 13px;">{_escape((p.get('created_at') or '')[:16])}</td>
                <td style="text-align: right;">{action_btn}</td>
            </tr>
            """
        if not rows:
            rows = "<tr><td colspan='6' class='empty-cell'>No payment requests submitted yet</td></tr>"
        return rows

    def build_users_rows(users: list) -> str:
        rows = ""
        for u in users:
            has_sub = False
            expiry_raw = u.get("subscription_expiry")
            if expiry_raw:
                try:
                    exp_dt = datetime.fromisoformat(expiry_raw)
                    if exp_dt > now:
                        has_sub = True
                except Exception:
                    has_sub = True

            if has_sub:
                plan_label = "FULL ACCESS"
                plan_style = "#34d399"
                status_html = """<span class="status-pill" style="color: #34d399; background: rgba(52, 211, 153, 0.1);">● ACTIVE</span>"""
                expiry_fmt = f"""<span style="color: #ffffff; font-weight: 500;">{_escape((expiry_raw or '')[:10])}</span>"""
            else:
                plan_label = "FREE TRIAL"
                plan_style = "#a1a1aa"
                used = u.get('trial_questions_used', 0) or 0
                if used >= FREE_TRIAL_QUESTIONS:
                    status_html = f"""<span class="status-pill" style="color: #f87171; background: rgba(248, 113, 113, 0.1);">PAYWALLED ({used}/{FREE_TRIAL_QUESTIONS})</span>"""
                else:
                    status_html = f"""<span class="status-pill" style="color: #fbbf24; background: rgba(251, 191, 36, 0.1);">TRIAL ({used}/{FREE_TRIAL_QUESTIONS})</span>"""
                expiry_fmt = '<span style="color: #71717a;">Free Trial</span>'

            user_cell, activate_target, _ = _format_user_cell(u)
            rows += f"""
            <tr>
                <td style="font-weight: 500; color: #f4f4f5;">{user_cell}</td>
                <td><span class="plan-pill" style="color: {plan_style};">{plan_label}</span></td>
                <td>{status_html}</td>
                <td>{expiry_fmt}</td>
                <td><code class="code-pill">{_escape(u.get('referral_code', 'N/A'))}</code> ({u.get('referral_count', 0)} refs)</td>
                <td style="color: #71717a; font-size: 12px;">{_escape((u.get('last_active') or '')[:16])}</td>
                <td style="text-align: right;">
                    <button onclick="openActivateModal('{_escape(activate_target)}')" class="btn-sm-action">Manage</button>
                </td>
            </tr>
            """
        if not rows:
            rows = "<tr><td colspan='7' class='empty-cell'>No users recorded yet</td></tr>"
        return rows

    # 3. Compile 100% Real Production Data & 14-Day Activity Series
    now = datetime.now()
    default_dates = [(now - timedelta(days=13 - i)).strftime("%b %d") for i in range(13)] + ["Today"]

    real_payments = metrics.get("recent_payments", [])
    real_users = metrics.get("all_users", [])

    real_payments_html = build_payments_rows(real_payments)
    real_users_html = build_users_rows(real_users)

    total_users = metrics.get("total_users", 0) or 0
    real_active_subs = metrics.get("active_subscribers", 0) or 0
    real_subs_percent = min(100, int((real_active_subs / total_users) * 100)) if total_users > 0 else 0

    real_mrr = metrics.get("estimated_mrr_pkr", 0) or 0
    mrr_bar_percent = 100 if real_mrr > 0 else 0

    total_queries = metrics.get("total_queries", 0) or 0
    avg_queries = round(total_queries / total_users, 1) if total_users > 0 else 0.0
    db_backend = metrics.get("db_backend") or "Live Database"
    wa_api_ready = bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID)

    chart_payload = {
        "dates": metrics.get("chart_dates") or default_dates,
        "queries": metrics.get("chart_queries") or [0] * 14,
        "new_users": metrics.get("chart_new_users") or [0] * 14,
    }
    chart_data_json = json.dumps(chart_payload)

    wa_broadcast_badge = (
        "<span style='font-size: 12px; color: #34d399; font-weight: 600;'>● WhatsApp Cloud API Ready</span>"
        if wa_api_ready
        else "<span style='font-size: 12px; color: #fbbf24; font-weight: 600;'>● WhatsApp Cloud API Not Configured</span>"
    )
    wa_broadcast_hint = (
        "Dispatches via Meta WhatsApp Cloud API to active subscribers with linked Pakistani mobile numbers."
        if wa_api_ready
        else "Set WHATSAPP_TOKEN and WHATSAPP_PHONE_NUMBER_ID in .env to enable live WhatsApp broadcasts."
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="theme-color" content="#0b0b0d">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Hassan Ali Farrukh AI — Management Hub</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-base: #0b0b0d;
            --bg-surface: #141416;
            --bg-card: #18181b;
            --bg-card-hover: #1f1f23;
            --border: rgba(255, 255, 255, 0.08);
            --border-subtle: rgba(255, 255, 255, 0.04);
            --text-primary: #ffffff;
            --text-secondary: #a1a1aa;
            --text-muted: #71717a;
            --accent-green: #22c55e;
            --accent-blue: #38bdf8;
            --accent-gold: #f59e0b;
            --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            --safe-bottom: env(safe-area-inset-bottom, 0px);
            --safe-top: env(safe-area-inset-top, 0px);
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }}

        html, body {{
            overscroll-behavior-y: none;
        }}

        button, a, [role="button"], .nav-item {{
            touch-action: manipulation;
            user-select: none;
            -webkit-user-select: none;
        }}

        .btn-primary:active, .btn-action:active, .btn-sm-action:active, .btn-refresh:active, .btn-logout:active, .nav-item:active {{
            transform: scale(0.97);
            transition: transform 80ms ease-out;
        }}

        body {{
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: var(--font-family);
            display: flex;
            min-height: 100vh;
            min-height: 100dvh;
            -webkit-font-smoothing: antialiased;
        }}

        /* Left Navigation Sidebar */
        .sidebar {{
            width: 250px;
            background: var(--bg-surface);
            border-right: 1px solid var(--border);
            padding: 24px 16px;
            display: flex;
            flex-direction: column;
            gap: 24px;
            flex-shrink: 0;
        }}

        .brand-logo {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 6px 8px;
            margin-bottom: 6px;
        }}

        .logo-box {{
            width: 36px;
            height: 36px;
            background: #ffffff;
            color: #0b0b0d;
            border-radius: 9px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 16px;
            box-shadow: 0 2px 10px rgba(255, 255, 255, 0.15);
            flex-shrink: 0;
        }}

        .profile-info {{
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}

        .profile-name {{
            font-size: 13.5px;
            font-weight: 600;
            color: var(--text-primary);
            letter-spacing: -0.2px;
            line-height: 1.25;
        }}

        .profile-badge {{
            font-size: 11px;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            font-weight: 500;
        }}

        .nav-menu {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .nav-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 9px 12px;
            font-size: 13px;
            font-weight: 500;
            color: var(--text-secondary);
            border-radius: 8px;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.15s ease;
            border: none;
            background: transparent;
            width: 100%;
            text-align: left;
        }}

        .nav-item:hover {{
            background: rgba(255, 255, 255, 0.04);
            color: var(--text-primary);
        }}

        .nav-item.active {{
            background: rgba(255, 255, 255, 0.08);
            color: var(--text-primary);
            font-weight: 600;
        }}

        .sidebar-footer {{
            margin-top: auto;
            padding: 12px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border-subtle);
            border-radius: 10px;
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .status-dot {{
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: var(--accent-green);
            box-shadow: 0 0 8px var(--accent-green);
        }}

        /* Main Workspace Content Area */
        .workspace {{
            flex: 1;
            padding: 32px 40px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 24px;
        }}

        .header-bar {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 20px;
            flex-wrap: wrap;
        }}

        .header-title h1 {{
            font-size: 22px;
            font-weight: 600;
            letter-spacing: -0.4px;
        }}

        .header-title p {{
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }}

        .header-actions {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .live-badge {{
            font-size: 11px;
            font-weight: 600;
            padding: 4px 9px;
            border-radius: 6px;
            background: rgba(34, 197, 94, 0.12);
            color: #22c55e;
            letter-spacing: 0.4px;
            border: 1px solid rgba(34, 197, 94, 0.25);
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .btn-refresh {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-secondary);
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 500;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.15s ease;
        }}

        .btn-refresh:hover {{
            background: var(--bg-card-hover);
            color: var(--text-primary);
        }}

        .btn-logout {{
            background: rgba(239, 68, 68, 0.08);
            border: 1px solid rgba(239, 68, 68, 0.25);
            color: #f87171;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 6px;
        }}

        .btn-logout:hover {{
            background: rgba(239, 68, 68, 0.18);
            color: #ffffff;
            border-color: rgba(239, 68, 68, 0.5);
        }}

        .btn-primary {{
            background: #ffffff;
            color: #0b0b0d;
            border: none;
            padding: 8px 16px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .btn-primary:hover {{
            background: #e4e4e7;
        }}

        /* Universal Tooltip Component */
        .info-tooltip {{
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            vertical-align: middle;
            margin-left: 6px;
            color: var(--text-muted);
            transition: color 0.15s ease;
            outline: none;
        }}

        .info-tooltip:hover, .info-tooltip:focus, .info-tooltip.active {{
            color: var(--text-primary);
        }}

        .info-icon {{
            width: 14px;
            height: 14px;
            opacity: 0.7;
        }}

        /* Smart Floating Tooltip (Attached to body) */
        .global-floating-tooltip {{
            position: fixed;
            background: #1f1f23;
            color: #f4f4f5;
            font-size: 11.5px;
            font-weight: 400;
            line-height: 1.45;
            padding: 9px 13px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.15);
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.7);
            width: max-content;
            max-width: 270px;
            white-space: normal;
            text-align: left;
            pointer-events: none;
            opacity: 0;
            visibility: hidden;
            transform: translateY(4px);
            transition: opacity 0.15s ease, transform 0.15s ease;
            z-index: 999999;
        }}

        .global-floating-tooltip.visible {{
            opacity: 1;
            visibility: visible;
            transform: translateY(0);
        }}

        /* Top Summary Cards */
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 16px;
        }}

        .summary-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 20px 22px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}

        .card-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 13px;
            color: var(--text-secondary);
            font-weight: 500;
        }}

        .card-value {{
            display: flex;
            align-items: baseline;
            gap: 8px;
        }}

        .card-value .main-num {{
            font-size: 26px;
            font-weight: 600;
            color: var(--text-primary);
            letter-spacing: -0.5px;
        }}

        .card-value .denom {{
            font-size: 14px;
            color: var(--text-muted);
            font-weight: 400;
        }}

        .progress-bar-bg {{
            width: 100%;
            height: 4px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 4px;
            overflow: hidden;
        }}

        .progress-bar-fill {{
            height: 100%;
            background: #ffffff;
            border-radius: 4px;
            transition: width 0.4s ease;
        }}

        .card-subtext {{
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
        }}

        /* Interactive Polyline Chart Card */
        .chart-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 22px;
            display: flex;
            flex-direction: column;
            gap: 18px;
        }}

        .chart-top-bar {{
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
        }}

        .chart-title-box h3 {{
            font-size: 15px;
            font-weight: 600;
            letter-spacing: -0.2px;
        }}

        .chart-legend-row {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .legend-chip {{
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 5px 11px;
            background: rgba(255, 255, 255, 0.03);
            border: 1px solid var(--border-subtle);
            border-radius: 6px;
            font-size: 11.5px;
            color: var(--text-secondary);
        }}

        .legend-line {{
            display: inline-block;
            width: 16px;
            height: 2px;
        }}

        .legend-line.solid-white {{
            background: #ffffff;
            box-shadow: 0 0 6px rgba(255, 255, 255, 0.4);
        }}

        .legend-line.dashed-gray {{
            border-top: 2px dashed #71717a;
        }}

        .stats-highlight-row {{
            display: flex;
            gap: 32px;
            padding: 4px 0;
            flex-wrap: wrap;
        }}

        .stat-item {{
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}

        .stat-number {{
            font-size: 22px;
            font-weight: 600;
            color: var(--text-primary);
            letter-spacing: -0.4px;
        }}

        .stat-label {{
            font-size: 12px;
            color: var(--text-muted);
        }}

        .chart-container {{
            position: relative;
            height: 220px;
            width: 100%;
        }}

        /* Section Tables */
        .section-split {{
            display: grid;
            grid-template-columns: 1.15fr 1fr;
            gap: 18px;
        }}

        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 14px;
            overflow: hidden;
            display: flex;
            flex-direction: column;
        }}

        .table-scroll-container {{
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: thin;
            scrollbar-color: rgba(255, 255, 255, 0.15) transparent;
        }}

        .table-scroll-container::-webkit-scrollbar {{
            height: 4px;
        }}

        .table-scroll-container::-webkit-scrollbar-thumb {{
            background: rgba(255, 255, 255, 0.15);
            border-radius: 4px;
        }}

        .table-card-header {{
            padding: 16px 20px;
            border-bottom: 1px solid var(--border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .table-card-header h3 {{
            font-size: 14px;
            font-weight: 600;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }}

        th {{
            background: rgba(255, 255, 255, 0.02);
            padding: 12px 14px;
            text-align: left;
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 500;
            border-bottom: 1px solid var(--border);
            white-space: nowrap;
        }}

        td {{
            padding: 13px 14px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            white-space: nowrap;
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        .empty-cell {{
            text-align: center;
            color: var(--text-muted);
            padding: 32px 16px;
            font-style: italic;
        }}

        .code-pill {{
            background: rgba(255, 255, 255, 0.06);
            padding: 3px 7px;
            border-radius: 5px;
            font-family: monospace;
            font-size: 12px;
            color: #e4e4e7;
        }}

        .plan-pill {{
            background: rgba(255, 255, 255, 0.06);
            color: #ffffff;
            font-weight: 600;
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 4px;
            letter-spacing: 0.5px;
        }}

        .status-pill {{
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }}

        .btn-action {{
            background: #ffffff;
            color: #09090b;
            border: none;
            padding: 5px 12px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }}

        .btn-action:hover {{
            background: #e4e4e7;
        }}

        .btn-sm-action {{
            background: rgba(255, 255, 255, 0.06);
            color: #ffffff;
            border: 1px solid var(--border);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 500;
            cursor: pointer;
        }}

        .btn-sm-action:hover {{
            background: rgba(255, 255, 255, 0.12);
        }}

        .btn-wa {{
            color: #34d399;
            text-decoration: none;
            font-size: 12px;
            font-weight: 600;
            padding: 5px 10px;
            background: rgba(52, 211, 153, 0.08);
            border-radius: 6px;
            display: inline-block;
            transition: all 0.15s ease;
        }}

        .btn-wa:hover {{
            background: rgba(52, 211, 153, 0.15);
        }}

        /* Broadcast & QR Tabs Styling */
        .broadcast-card {{
            max-width: 650px;
            width: 100%;
        }}

        .card-content-box {{
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
        }}

        .broadcast-actions {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-top: 8px;
            gap: 12px;
        }}

        .input-group {{
            display: flex;
            flex-direction: column;
            gap: 6px;
        }}

        .input-label {{
            font-size: 12px;
            color: var(--text-secondary);
            font-weight: 500;
        }}

        .input-field, .textarea-field, .select-field {{
            background: #111113;
            border: 1px solid var(--border);
            color: #ffffff;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            outline: none;
            font-family: inherit;
        }}

        .input-field:focus, .textarea-field:focus, .select-field:focus {{
            border-color: rgba(255, 255, 255, 0.3);
        }}

        /* Modal Dialog Styling */
        .modal-overlay {{
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            -webkit-backdrop-filter: blur(4px);
            z-index: 10000;
            align-items: center;
            justify-content: center;
            padding: 16px;
            box-sizing: border-box;
        }}

        .modal-dialog {{
            background: #18181b;
            border: 1px solid var(--border);
            border-radius: 14px;
            width: 100%;
            max-width: 400px;
            padding: 24px 20px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.8);
            max-height: 90vh;
            overflow-y: auto;
        }}

        /* Tab Page Visibility */
        .tab-content {{
            display: none;
            flex-direction: column;
            gap: 24px;
        }}

        .tab-content.active {{
            display: flex;
        }}

        .nav-label-mobile {{
            display: none;
        }}

        /* Tablet Responsive (769px - 1024px) */
        @media (min-width: 769px) and (max-width: 1024px) {{
            .summary-grid {{
                grid-template-columns: 1fr;
            }}
            .section-split {{
                grid-template-columns: 1fr;
            }}
            .sidebar {{
                width: 70px;
                padding: 16px 8px;
            }}
            .brand-title, .profile-info, .sidebar-footer span:last-child {{
                display: none;
            }}
            .nav-item span {{
                display: none;
            }}
            .workspace {{
                padding: 20px 16px;
            }}
        }}

        /* Mobile Native Responsive (<= 768px) */
        @media (max-width: 768px) {{
            body {{
                flex-direction: column;
                overflow-x: hidden;
            }}

            /* Executive Top Bar */
            .sidebar {{
                position: sticky;
                top: 0;
                width: 100%;
                height: auto;
                padding: 12px 16px;
                border-right: none;
                border-bottom: 1px solid var(--border);
                background: #141416;
                backdrop-filter: none;
                -webkit-backdrop-filter: none;
                flex-direction: row;
                align-items: center;
                justify-content: space-between;
                gap: 0;
                z-index: 1000;
            }}

            .brand-logo {{
                margin-bottom: 0;
                padding: 0;
                gap: 10px;
            }}

            .logo-box {{
                width: 32px;
                height: 32px;
                border-radius: 8px;
                font-size: 15px;
            }}

            .profile-info {{
                display: flex !important;
            }}

            .profile-name {{
                font-size: 13px;
            }}

            .profile-badge {{
                font-size: 10px;
            }}

            .sidebar-footer {{
                margin-top: 0;
                padding: 5px 9px;
                border-radius: 6px;
                font-size: 11px;
                display: flex !important;
                gap: 6px;
            }}

            /* iOS-Style Floating Glass Bottom Navigation Bar */
            .nav-menu {{
                position: fixed;
                bottom: 0;
                left: 0;
                right: 0;
                width: 100%;
                height: calc(58px + var(--safe-bottom));
                background: rgba(18, 18, 20, 0.96);
                backdrop-filter: blur(20px);
                -webkit-backdrop-filter: blur(20px);
                border-top: 1px solid var(--border);
                display: flex;
                flex-direction: row;
                justify-content: space-around;
                align-items: center;
                padding: 4px 6px calc(4px + var(--safe-bottom)) 6px;
                z-index: 9999;
                box-shadow: 0 -8px 25px rgba(0, 0, 0, 0.6);
            }}

            .nav-item {{
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 5px 2px;
                gap: 3px;
                border-radius: 8px;
                text-align: center;
                flex: 1;
                min-width: 0;
                color: var(--text-muted);
            }}

            .nav-item.active {{
                background: rgba(255, 255, 255, 0.08);
                color: #ffffff;
            }}

            .nav-item svg {{
                width: 18px;
                height: 18px;
            }}

            .nav-label-desktop {{
                display: none !important;
            }}

            .nav-label-mobile {{
                display: block !important;
                font-size: 9.5px;
                line-height: 1.1;
                font-weight: 500;
                letter-spacing: -0.2px;
            }}

            /* Full-width Workspace */
            .workspace {{
                width: 100%;
                max-width: 100vw;
                box-sizing: border-box;
                overflow-x: hidden;
                padding: 16px 14px calc(80px + var(--safe-bottom)) 14px;
                gap: 16px;
            }}

            .header-bar {{
                flex-direction: column;
                align-items: stretch;
                gap: 14px;
            }}

            .header-title h1 {{
                font-size: 20px;
                letter-spacing: -0.3px;
            }}

            .header-title p {{
                font-size: 12.5px;
                line-height: 1.4;
            }}

            .header-actions {{
                width: 100%;
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px;
            }}

            .header-activate-btn {{
                grid-column: 1 / -1;
                padding: 11px 16px;
                font-size: 13.5px;
                text-align: center;
                justify-content: center;
                display: flex;
                align-items: center;
            }}

            .btn-refresh {{
                text-align: center;
                padding: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 12.5px;
            }}

            .btn-logout {{
                text-align: center;
                padding: 10px;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 12.5px;
            }}

            .summary-grid {{
                grid-template-columns: 1fr;
                gap: 12px;
            }}

            .summary-card {{
                padding: 16px;
                border-radius: 12px;
                gap: 10px;
            }}

            .card-value .main-num {{
                font-size: 24px;
            }}

            .section-split {{
                grid-template-columns: 1fr;
                gap: 14px;
            }}

            .chart-card {{
                padding: 16px;
                border-radius: 12px;
                gap: 14px;
            }}

            .stats-highlight-row {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 12px 10px;
            }}

            .stat-number {{
                font-size: 20px;
            }}

            .stat-label {{
                font-size: 11px;
                line-height: 1.3;
            }}

            .chart-container {{
                height: 190px;
            }}

            .table-card {{
                border-radius: 12px;
            }}

            .table-card-header {{
                padding: 12px 14px;
                gap: 8px;
                flex-wrap: wrap;
            }}

            .table-card-header h3 {{
                font-size: 13.5px;
            }}

            th {{
                padding: 10px 12px;
                font-size: 11.5px;
            }}

            td {{
                padding: 11px 12px;
                font-size: 12px;
            }}

            .btn-action {{
                padding: 6px 10px;
                font-size: 11.5px;
            }}

            .btn-sm-action {{
                padding: 5px 8px;
                font-size: 11px;
            }}

            .btn-wa {{
                padding: 6px 10px;
                font-size: 11.5px;
            }}

            .broadcast-card {{
                max-width: 100%;
            }}

            .card-content-box {{
                padding: 16px;
                gap: 14px;
            }}

            .broadcast-actions {{
                display: flex;
                flex-direction: column-reverse;
                align-items: stretch;
                gap: 10px;
                margin-top: 10px;
            }}

            .broadcast-actions button {{
                width: 100%;
                padding: 12px;
                font-size: 13.5px;
            }}

            .broadcast-actions span {{
                text-align: center;
                font-size: 11.5px;
            }}

            .input-field, .textarea-field, .select-field {{
                font-size: 16px !important;
            }}
        }}
    </style>
</head>
<body>

    <!-- Sidebar with Tab Buttons -->
    <aside class="sidebar">
        <div class="brand-logo">
            <div class="logo-box">H</div>
            <div class="profile-info">
                <span class="profile-name">Hassan AI</span>
                <span class="profile-badge">Admin Hub</span>
            </div>
        </div>

        <nav class="nav-menu">
            <button onclick="switchTab('overview')" id="tab-btn-overview" class="nav-item active">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"></rect><rect x="14" y="3" width="7" height="7"></rect><rect x="14" y="14" width="7" height="7"></rect><rect x="3" y="14" width="7" height="7"></rect></svg>
                <span class="nav-label-desktop">Overview</span>
                <span class="nav-label-mobile">Overview</span>
            </button>
            <button onclick="switchTab('subscribers')" id="tab-btn-subscribers" class="nav-item">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"></path><circle cx="9" cy="7" r="4"></circle><path d="M23 21v-2a4 4 0 0 0-3-3.87"></path><path d="M16 3.13a4 4 0 0 1 0 7.75"></path></svg>
                <span class="nav-label-desktop">Subscribers &amp; Trials</span>
                <span class="nav-label-mobile">Subs</span>
            </button>
            <button onclick="switchTab('payments')" id="tab-btn-payments" class="nav-item">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="1" x2="12" y2="23"></line><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"></path></svg>
                <span class="nav-label-desktop">Payment Approvals</span>
                <span class="nav-label-mobile">Payments</span>
            </button>
            <button onclick="switchTab('broadcast')" id="tab-btn-broadcast" class="nav-item">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path></svg>
                <span class="nav-label-desktop">Broadcast Tool</span>
                <span class="nav-label-mobile">Broadcast</span>
            </button>
        </nav>

        <div class="sidebar-footer">
            <div style="display:flex; align-items:center; gap:8px;">
                <span class="status-dot"></span>
                <span>{_escape(db_backend)}</span>
            </div>
            <span style="color:#ffffff; font-weight:600;">LIVE</span>
        </div>
    </aside>

    <!-- Main Workspace Area -->
    <main class="workspace">

        <!-- TAB 1: OVERVIEW -->
        <section id="tab-overview" class="tab-content active">
            <div class="header-bar">
                <div class="header-title">
                    <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
                        <h1>Executive Overview</h1>
                        <span class="live-badge">● LIVE DATA</span>
                    </div>
                    <p>Real-time {_escape(db_backend)} metrics for registered users, EasyPaisa/Bank payments, and AI activity.</p>
                </div>
                
                <div class="header-actions">
                    <button onclick="openActivateModal('')" class="btn-primary header-activate-btn">+ Quick Activate</button>
                    <a href="/admin/dashboard" class="btn-refresh">↻ Refresh</a>
                    <button onclick="handleAdminLogout()" class="btn-logout" title="Lock Executive Hub">⎋ Lock / Sign Out</button>
                </div>
            </div>

            <!-- Top Summary Cards with Info Icons -->
            <div class="summary-grid">
                <!-- Card 1 -->
                <div class="summary-card">
                    <div class="card-header">
                        <div>
                            <span>Active Subscribers</span>
                            {info_badge(f"Users with an unexpired Full Access monthly pass (PKR {MONTHLY_PRICE_PKR:,}/mo).")}
                        </div>
                        <span style="color: var(--text-primary);"><span id="card-conversion-rate">{metrics['conversion_rate']}</span>% paid {info_badge("Percentage of all registered users who currently have an active paid subscription.")}</span>
                    </div>
                    <div class="card-value">
                        <span class="main-num" id="card-active-subs">{real_active_subs}</span>
                        <span class="denom">/ {total_users} Total Users</span>
                    </div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: {real_subs_percent}%;"></div>
                    </div>
                    <div class="card-subtext">
                        <span>Full Access: <strong style="color:#ffffff;">PKR {MONTHLY_PRICE_PKR:,}/mo</strong></span>
                        <span>Active Trials: <strong style="color:#ffffff;">{metrics['active_trials']}</strong></span>
                    </div>
                </div>

                <!-- Card 2 -->
                <div class="summary-card">
                    <div class="card-header">
                        <div>
                            <span>Active Monthly Revenue</span>
                            {info_badge(f"Computed directly from current active subscribers multiplied by PKR {MONTHLY_PRICE_PKR:,}/mo.")}
                        </div>
                        <span style="color: #34d399; font-weight: 600;">{metrics['pending_payments']} Pending TRX</span>
                    </div>
                    <div class="card-value">
                        <span class="main-num" id="card-mrr">PKR {real_mrr:,}</span>
                        <span class="denom">/ month</span>
                    </div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: {mrr_bar_percent}%; background: #34d399;"></div>
                    </div>
                    <div class="card-subtext">
                        <span>Rate: <strong style="color:#ffffff;">PKR {MONTHLY_PRICE_PKR:,} / member</strong></span>
                        <span>Paywalled: <strong style="color:#ffffff;">{metrics['paywalled_trials']}</strong></span>
                    </div>
                </div>

                <!-- Card 3 -->
                <div class="summary-card">
                    <div class="card-header">
                        <div>
                            <span>Audience &amp; Engagement</span>
                            {info_badge("Live counts across all verified web users and linked Pakistani mobile numbers.")}
                        </div>
                        <span style="color: var(--accent-blue); font-weight: 600;">{avg_queries} avg / user</span>
                    </div>
                    <div class="card-value">
                        <span class="main-num" id="card-total-users">{total_users}</span>
                        <span class="denom">Registered Users</span>
                    </div>
                    <div class="progress-bar-bg">
                        <div class="progress-bar-fill" style="width: 100%; background: #38bdf8;"></div>
                    </div>
                    <div class="card-subtext">
                        <span>Total Queries: <strong style="color:#ffffff;">{total_queries}</strong></span>
                        <span>Referrals: <strong style="color:#ffffff;">{metrics['total_referrals']}</strong> {info_badge("Total credited referrals across all users. 3 paid referrals award 30 free days automatically.")}</span>
                    </div>
                </div>
            </div>

            <!-- Analytics Chart Card (100% Real 14-Day Database Counts) -->
            <div class="chart-card">
                <div class="chart-top-bar">
                    <div class="chart-title-box">
                        <h3>14-Day Live Activity</h3>
                        <p style="font-size: 12px; color: var(--text-muted); margin-top: 3px;">
                            Actual daily AI responses logged in chat history vs. new user registrations over the past 14 days.
                        </p>
                    </div>
                    <div class="chart-legend-row">
                        <div class="legend-chip"><span class="legend-line solid-white"></span><span>Daily AI Responses</span></div>
                        <div class="legend-chip"><span class="legend-line dashed-gray"></span><span>New Users</span></div>
                    </div>
                </div>

                <div class="stats-highlight-row">
                    <div class="stat-item">
                        <span class="stat-number" id="stat-total-queries">{total_queries}</span>
                        <span class="stat-label">Total AI Queries {info_badge("Total lifetime queries recorded across all users in the database.")}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number" id="stat-total-users">{total_users}</span>
                        <span class="stat-label">Registered Users {info_badge("Total unique user accounts in the database.")}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number" id="stat-active-trials">{metrics['active_trials']}</span>
                        <span class="stat-label">Active Free Trials {info_badge(f"Unsubscribed users with fewer than {FREE_TRIAL_QUESTIONS} trial questions used.")}</span>
                    </div>
                    <div class="stat-item">
                        <span class="stat-number" id="stat-paywalled-trials">{metrics['paywalled_trials']}</span>
                        <span class="stat-label">Paywalled Trials {info_badge(f"Unsubscribed users who have used all {FREE_TRIAL_QUESTIONS} free questions.")}</span>
                    </div>
                </div>

                <div class="chart-container">
                    <canvas id="analyticsChart"></canvas>
                </div>
            </div>

            <!-- Recent Payment Requests Table (Full Width) -->
            <div class="table-card">
                <div class="table-card-header">
                    <div>
                        <h3>Payment Requests (TRX)</h3>
                        {info_badge(f"Incoming PKR {MONTHLY_PRICE_PKR:,}/mo transactions submitted via EasyPaisa, Allied Bank, or Raast. Click 'Approve & Activate' to grant Full Access.")}
                    </div>
                    <span class="code-pill" id="badge-pending-payments">{metrics['pending_payments']} Pending</span>
                </div>
                <div class="table-scroll-container">
                    <table>
                        <thead>
                            <tr>
                                <th>User (Phone / Email)</th>
                                <th>TRX ID</th>
                                <th>Plan</th>
                                <th>Status</th>
                                <th>Time</th>
                                <th style="text-align: right;">Action</th>
                            </tr>
                        </thead>
                        <tbody id="overview-payments-tbody">
                            {real_payments_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 2: SUBSCRIBERS DIRECTORY -->
        <section id="tab-subscribers" class="tab-content">
            <div class="header-bar">
                <div class="header-title">
                    <h1>Subscribers &amp; Free Trial Directory</h1>
                    <p>Live registry of registered accounts, membership status, trial usage, and referral codes.</p>
                </div>
                <div class="header-actions">
                    <button onclick="openActivateModal('')" class="btn-primary header-activate-btn">+ Activate / Extend User</button>
                </div>
            </div>

            <div class="table-card">
                <div class="table-card-header">
                    <h3>All Users (50 Most Active)</h3>
                    <span class="code-pill" id="badge-total-users">{total_users} Total Registered</span>
                </div>
                <div class="table-scroll-container">
                    <table>
                        <thead>
                            <tr>
                                <th>User (Phone / Email)</th>
                                <th>Plan</th>
                                <th>Status</th>
                                <th>Expiry Date</th>
                                <th>Referral Code</th>
                                <th>Last Active</th>
                                <th style="text-align: right;">Action</th>
                            </tr>
                        </thead>
                        <tbody id="subscribers-users-tbody">
                            {real_users_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 3: PAYMENTS REVIEW -->
        <section id="tab-payments" class="tab-content">
            <div class="header-bar">
                <div class="header-title">
                    <h1>Payment Approvals (TRX)</h1>
                    <p>Audit incoming EasyPaisa, Allied Bank, and Raast Transaction IDs (PKR {MONTHLY_PRICE_PKR:,}/mo Full Access) and approve in one click.</p>
                </div>
            </div>

            <div class="table-card">
                <div class="table-card-header">
                    <h3>Recent Transactions</h3>
                    <span class="code-pill" id="badge-pending-payments-tab">{metrics['pending_payments']} Pending Approval</span>
                </div>
                <div class="table-scroll-container">
                    <table>
                        <thead>
                            <tr>
                                <th>User (Phone / Email)</th>
                                <th>TRX ID</th>
                                <th>Plan</th>
                                <th>Status</th>
                                <th>Date / Time</th>
                                <th style="text-align: right;">Action</th>
                            </tr>
                        </thead>
                        <tbody id="payments-tab-tbody">
                            {real_payments_html}
                        </tbody>
                    </table>
                </div>
            </div>
        </section>

        <!-- TAB 4: BROADCAST TOOL -->
        <section id="tab-broadcast" class="tab-content">
            <div class="header-bar">
                <div class="header-title">
                    <h1>Broadcast Composer</h1>
                    <p>Send announcements via Meta WhatsApp Cloud API to active Full Access subscribers with linked Pakistani mobile numbers.</p>
                </div>
            </div>

            <div class="table-card broadcast-card">
                <div class="table-card-header">
                    <h3>Compose Broadcast</h3>
                    {wa_broadcast_badge}
                </div>
                <div class="card-content-box">
                    <div class="input-group">
                        <label class="input-label">Target Audience</label>
                        <div class="input-field" style="color: #a1a1aa;">
                            All Active Subscribers — Full Access ({metrics['active_subscribers']})
                        </div>
                    </div>

                    <div class="input-group">
                        <label class="input-label">Broadcast Message</label>
                        <textarea id="broadcast-message" class="textarea-field" rows="5" placeholder="Write the announcement you want to send to active subscribers..."></textarea>
                    </div>

                    <div class="broadcast-actions">
                        <span style="font-size: 12px; color: var(--text-muted);">{wa_broadcast_hint}</span>
                        <button onclick="sendBroadcast()" class="btn-primary">Send Broadcast 🚀</button>
                    </div>
                    <div id="broadcast-status" style="font-size: 13px; margin-top: 10px;"></div>
                </div>
            </div>
        </section>

    </main>

    <!-- Modal for Manual Activation / Extension -->
    <div id="activate-modal" class="modal-overlay">
        <div class="modal-dialog">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="font-size:16px; font-weight:600;">Activate / Extend Subscription</h3>
                <button onclick="closeActivateModal()" style="background:none; border:none; color:#a1a1aa; font-size:20px; cursor:pointer; padding:4px 8px;">✕</button>
            </div>
            <div style="background: rgba(52, 211, 153, 0.08); border: 1px solid rgba(52, 211, 153, 0.25); border-radius: 10px; padding: 10px 12px; display: flex; justify-content: space-between; align-items: center;">
                <span style="font-size: 12.5px; font-weight: 600; color: #34d399;">👑 Full Access Pass</span>
                <span style="font-size: 13px; font-weight: 700; color: #ffffff;">PKR {MONTHLY_PRICE_PKR:,} / month</span>
            </div>
            <div class="input-group">
                <label class="input-label">User Pakistani Phone or Email</label>
                <input id="modal-phone" type="text" class="input-field" placeholder="e.g. 03007560313, +923007560313, or user@gmail.com">
            </div>
            <div class="input-group">
                <label class="input-label">Days to Add</label>
                <input id="modal-days" type="number" inputmode="numeric" class="input-field" value="30">
            </div>
            <button onclick="submitModalActivate()" class="btn-primary" style="margin-top:8px; padding:12px; font-size:14px;">Activate Full Access</button>
        </div>
    </div>

    <!-- Chart.js & Interactive Controller Scripts -->
    <script>
        const CHART_DATA = {chart_data_json};
        let analyticsChartInstance = null;

        // Tab switching with URL hash & localStorage persistence
        function switchTab(tabId) {{
            if (!tabId) tabId = 'overview';
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            
            const target = document.getElementById('tab-' + tabId);
            const btn = document.getElementById('tab-btn-' + tabId);
            if (target) target.classList.add('active');
            if (btn) btn.classList.add('active');
            try {{
                localStorage.setItem('hassan_dashboard_tab', tabId);
                history.replaceState(null, '', '#' + tabId);
            }} catch (e) {{}}
            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        // Smart Universal Floating Tooltip
        const tooltipEl = document.createElement('div');
        tooltipEl.className = 'global-floating-tooltip';
        document.body.appendChild(tooltipEl);

        let activeTooltipTarget = null;

        function showTooltip(target) {{
            const text = target.getAttribute('data-tooltip');
            if (!text) return;

            tooltipEl.textContent = text;
            tooltipEl.classList.add('visible');
            activeTooltipTarget = target;

            const rect = target.getBoundingClientRect();
            const tipRect = tooltipEl.getBoundingClientRect();

            let top = rect.top - tipRect.height - 8;
            let left = rect.left + (rect.width / 2) - (tipRect.width / 2);

            if (top < 12) {{
                top = rect.bottom + 8;
            }}
            const rightMargin = 16;
            if (left + tipRect.width > window.innerWidth - rightMargin) {{
                left = window.innerWidth - tipRect.width - rightMargin;
            }}
            if (left < 16) {{
                left = 16;
            }}

            tooltipEl.style.top = top + 'px';
            tooltipEl.style.left = left + 'px';
        }}

        function hideTooltip() {{
            tooltipEl.classList.remove('visible');
            activeTooltipTarget = null;
        }}

        function bindTooltips() {{
            document.querySelectorAll('.info-tooltip').forEach(el => {{
                el.removeEventListener('mouseenter', el._enterHandler);
                el.removeEventListener('mouseleave', hideTooltip);
                el.removeEventListener('click', el._clickHandler);

                el._enterHandler = () => showTooltip(el);
                el._clickHandler = (e) => {{
                    e.stopPropagation();
                    if (tooltipEl.classList.contains('visible') && activeTooltipTarget === el) {{
                        hideTooltip();
                    }} else {{
                        showTooltip(el);
                    }}
                }};

                el.addEventListener('mouseenter', el._enterHandler);
                el.addEventListener('mouseleave', hideTooltip);
                el.addEventListener('click', el._clickHandler);
            }});
        }}

        document.addEventListener('click', hideTooltip);
        window.addEventListener('scroll', hideTooltip, {{ passive: true }});
        window.addEventListener('resize', hideTooltip, {{ passive: true }});

        // Quick Activation helper (accepts any Pakistani phone format or email)
        async function quickActivate(identifier) {{
            const res = await fetch('/api/admin/activate', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ identifier: identifier, days: 30 }})
            }});
            const data = await res.json().catch(() => ({{}}));
            if (res.ok && data.status === 'ok') {{
                window.location.reload();
            }} else {{
                alert(data.message || 'Activation failed');
            }}
        }}

        // Modal Helpers
        function openActivateModal(identifier) {{
            document.getElementById('modal-phone').value = identifier;
            document.getElementById('activate-modal').style.display = 'flex';
        }}
        function closeActivateModal() {{
            document.getElementById('activate-modal').style.display = 'none';
        }}
        async function submitModalActivate() {{
            const identifier = document.getElementById('modal-phone').value.trim();
            const days = parseInt(document.getElementById('modal-days').value || '30', 10);
            if (!identifier) return alert('Please enter a Pakistani phone number or email');

            const res = await fetch('/api/admin/activate', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ identifier: identifier, days: days }})
            }});
            const data = await res.json().catch(() => ({{}}));
            if (res.ok && data.status === 'ok') {{
                closeActivateModal();
                window.location.reload();
            }} else {{
                alert(data.message || 'Activation error');
            }}
        }}

        // Executive Lock / Logout Helper
        async function handleAdminLogout() {{
            try {{
                await fetch('/api/admin/logout', {{ method: 'POST' }});
            }} catch (e) {{}}
            window.location.href = '/admin/dashboard';
        }}

        // Broadcast Helper
        async function sendBroadcast() {{
            const msg = document.getElementById('broadcast-message').value.trim();
            const statusEl = document.getElementById('broadcast-status');
            if (!msg) return alert('Please type a message');

            statusEl.innerHTML = '<span style="color:#fbbf24;">Sending broadcast...</span>';
            const res = await fetch('/api/admin/broadcast', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ tier: 'all', message: msg }})
            }});
            const data = await res.json().catch(() => ({{}}));
            if (res.ok && data.status === 'ok') {{
                statusEl.innerHTML = `<span style="color:#34d399;">✓ Broadcast delivered to ${{data.count}} subscriber(s).</span>`;
                document.getElementById('broadcast-message').value = '';
            }} else {{
                statusEl.textContent = data.message || 'Failed to dispatch broadcast.';
                statusEl.style.color = '#ef4444';
            }}
        }}

        // Initialize Real 14-Day Analytics Chart on DOMContentLoaded
        document.addEventListener('DOMContentLoaded', () => {{
            const ctx = document.getElementById('analyticsChart');
            if (ctx) {{
                analyticsChartInstance = new Chart(ctx.getContext('2d'), {{
                    type: 'line',
                    data: {{
                        labels: CHART_DATA.dates,
                        datasets: [
                            {{
                                label: 'Daily AI Responses',
                                data: CHART_DATA.queries,
                                borderColor: '#ffffff',
                                backgroundColor: 'rgba(255, 255, 255, 0.04)',
                                fill: true,
                                tension: 0.35,
                                borderWidth: 2,
                                pointBackgroundColor: '#ffffff',
                                pointBorderColor: '#0b0b0d',
                                pointBorderWidth: 2,
                                pointRadius: 4,
                                pointHoverRadius: 6,
                            }},
                            {{
                                label: 'New Users',
                                data: CHART_DATA.new_users,
                                borderColor: '#71717a',
                                borderDash: [4, 4],
                                fill: false,
                                tension: 0.35,
                                borderWidth: 1.5,
                                pointRadius: 3,
                                pointBackgroundColor: '#71717a',
                            }}
                        ]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        interaction: {{
                            mode: 'index',
                            intersect: false,
                        }},
                        plugins: {{
                            legend: {{ display: false }},
                            tooltip: {{
                                backgroundColor: '#18181b',
                                borderColor: 'rgba(255, 255, 255, 0.12)',
                                borderWidth: 1,
                                titleColor: '#ffffff',
                                bodyColor: '#a1a1aa',
                                padding: 12,
                                cornerRadius: 8,
                                displayColors: true,
                                callbacks: {{
                                    title: function(items) {{
                                        return '📅 ' + items[0].label;
                                    }},
                                    label: function(item) {{
                                        if (item.datasetIndex === 0) {{
                                            return '  ● Daily AI Responses: ' + item.parsed.y;
                                        }} else {{
                                            return '  ┄ New Users: ' + item.parsed.y;
                                        }}
                                    }}
                                }}
                            }}
                        }},
                        scales: {{
                            x: {{
                                grid: {{ display: false, drawBorder: false }},
                                ticks: {{ color: '#71717a', font: {{ size: 11 }} }},
                                title: {{
                                    display: true,
                                    text: '14-Day Calendar Timeline (Live DB)',
                                    color: '#52525b',
                                    font: {{ size: 11, weight: '500' }},
                                    padding: {{ top: 8 }}
                                }}
                            }},
                            y: {{
                                grid: {{ color: 'rgba(255, 255, 255, 0.04)', drawBorder: false }},
                                ticks: {{ color: '#71717a', font: {{ size: 11 }}, precision: 0 }},
                                beginAtZero: true,
                                title: {{
                                    display: true,
                                    text: 'Daily Count',
                                    color: '#52525b',
                                    font: {{ size: 11, weight: '500' }},
                                    padding: {{ bottom: 8 }}
                                }}
                            }}
                        }}
                    }}
                }});
            }}

            // Restore saved active tab from hash or localStorage
            try {{
                localStorage.removeItem('hassan_dashboard_mode');
                const hashTab = window.location.hash.replace('#', '');
                const savedTab = hashTab || localStorage.getItem('hassan_dashboard_tab');
                if (savedTab && document.getElementById('tab-' + savedTab)) {{
                    switchTab(savedTab);
                }}
            }} catch (e) {{}}

            bindTooltips();
        }});
    </script>
</body>
</html>"""
    return html
