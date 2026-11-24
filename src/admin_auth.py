import hmac
import hashlib
import time
from fastapi import Request
from src.config import ADMIN_PASSWORD, ADMIN_SESSION_SECRET

MAX_SESSION_AGE_SECONDS = 7 * 24 * 3600  # 7 days

def _compute_signature(timestamp: str) -> str:
    message = f"{timestamp}:{ADMIN_SESSION_SECRET}:{ADMIN_PASSWORD}".encode("utf-8")
    key = ADMIN_SESSION_SECRET.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()

def generate_admin_session_token() -> str:
    """Generates a tamper-proof 7-day signed session token."""
    ts = str(int(time.time()))
    sig = _compute_signature(ts)
    return f"{ts}.{sig}"

def verify_admin_session_token(token: str | None) -> bool:
    """Verifies that the session token is valid and not expired."""
    if not token or "." not in token:
        return False
    try:
        ts, sig = token.split(".", 1)
        created_time = int(ts)
        now = int(time.time())
        if now - created_time > MAX_SESSION_AGE_SECONDS or created_time > now + 300:
            return False
        expected_sig = _compute_signature(ts)
        return hmac.compare_digest(sig, expected_sig)
    except Exception:
        return False

def is_admin_authenticated(request: Request) -> bool:
    """Checks whether the request carries a valid admin_session cookie."""
    cookie = request.cookies.get("admin_session")
    return verify_admin_session_token(cookie)

def render_admin_login_html(error: str = "") -> str:
    """Renders the Cursor-themed executive passkey login gate."""
    error_block = f"""
    <div class="login-alert error-shake">
        <span>⚠️</span>
        <span>{error}</span>
    </div>
    """ if error else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <meta name="theme-color" content="#0b0b0d">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Executive Passkey — Hassan Ali Farrukh</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0b0b0d;
            --bg-surface: #141416;
            --bg-card: #18181b;
            --border: rgba(255, 255, 255, 0.08);
            --border-focus: rgba(34, 197, 94, 0.45);
            --text-primary: #ffffff;
            --text-secondary: #a1a1aa;
            --text-muted: #71717a;
            --accent-green: #22c55e;
            --accent-green-glow: rgba(34, 197, 94, 0.25);
            --danger: #ef4444;
            --radius-md: 12px;
            --radius-lg: 16px;
            --font-sans: 'Plus Jakarta Sans', -apple-system, sans-serif;
            --font-mono: 'JetBrains Mono', monospace;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            -webkit-tap-highlight-color: transparent;
        }}

        body {{
            min-height: 100vh;
            min-height: 100dvh;
            background-color: var(--bg-base);
            color: var(--text-primary);
            font-family: var(--font-sans);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: calc(20px + env(safe-area-inset-top, 0px)) 20px calc(20px + env(safe-area-inset-bottom, 0px)) 20px;
            overscroll-behavior-y: none;
            -webkit-font-smoothing: antialiased;
            background-image: 
                radial-gradient(ellipse 60% 50% at 50% 0%, rgba(34, 197, 94, 0.08) 0%, transparent 70%),
                radial-gradient(circle at 50% 100%, rgba(56, 189, 248, 0.04) 0%, transparent 60%);
        }}

        @media (max-width: 480px) {{
            .login-card {{
                padding: 28px 20px !important;
            }}
            .form-input {{
                font-size: 16px !important;
            }}
        }}

        .login-card {{
            width: 100%;
            max-width: 410px;
            background: var(--bg-surface);
            border: 1px solid var(--border);
            border-radius: var(--radius-lg);
            padding: 36px 32px;
            box-shadow: 0 24px 60px rgba(0, 0, 0, 0.65);
            text-align: center;
        }}

        .logo-box {{
            width: 48px;
            height: 48px;
            background: #ffffff;
            color: #0b0b0d;
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 20px;
            font-weight: 800;
            margin: 0 auto 20px auto;
            letter-spacing: -0.5px;
            box-shadow: 0 8px 24px rgba(255, 255, 255, 0.15);
        }}

        .login-title {{
            font-size: 20px;
            font-weight: 700;
            letter-spacing: -0.3px;
            margin-bottom: 6px;
            color: var(--text-primary);
        }}

        .login-subtitle {{
            font-size: 13px;
            color: var(--text-muted);
            margin-bottom: 24px;
            line-height: 1.4;
        }}

        .login-alert {{
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.3);
            color: #fca5a5;
            padding: 10px 14px;
            border-radius: var(--radius-md);
            font-size: 12.5px;
            margin-bottom: 20px;
            display: flex;
            align-items: center;
            gap: 8px;
            text-align: left;
        }}

        @keyframes shake {{
            0%, 100% {{ transform: translateX(0); }}
            20%, 60% {{ transform: translateX(-6px); }}
            40%, 80% {{ transform: translateX(6px); }}
        }}

        .error-shake {{
            animation: shake 0.4s ease-in-out;
        }}

        .form-group {{
            margin-bottom: 20px;
            text-align: left;
        }}

        .form-label {{
            display: block;
            font-size: 11.5px;
            font-weight: 600;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.6px;
            margin-bottom: 8px;
        }}

        .input-wrapper {{
            position: relative;
        }}

        .form-input {{
            width: 100%;
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: var(--radius-md);
            padding: 13px 44px 13px 14px;
            font-size: 14px;
            color: var(--text-primary);
            font-family: var(--font-mono);
            outline: none;
            transition: border-color 0.15s, box-shadow 0.15s;
        }}

        .form-input:focus {{
            border-color: var(--accent-green);
            box-shadow: 0 0 0 3px var(--accent-green-glow);
        }}

        .eye-toggle-btn {{
            position: absolute;
            right: 12px;
            top: 50%;
            transform: translateY(-50%);
            background: none;
            border: none;
            color: var(--text-muted);
            cursor: pointer;
            padding: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
        }}

        .eye-toggle-btn:hover {{
            color: var(--text-primary);
        }}

        .submit-btn {{
            width: 100%;
            background: linear-gradient(135deg, #22c55e 0%, #16a34a 100%);
            color: #0b0b0d;
            border: none;
            border-radius: var(--radius-md);
            padding: 13px;
            font-size: 14px;
            font-weight: 700;
            cursor: pointer;
            box-shadow: 0 4px 16px rgba(34, 197, 94, 0.35);
            transition: transform 0.15s, filter 0.15s;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
        }}

        .submit-btn:hover {{
            filter: brightness(1.08);
            transform: translateY(-1px);
        }}

        .submit-btn:active {{
            transform: scale(0.98);
        }}

        .login-footer {{
            margin-top: 24px;
            padding-top: 18px;
            border-top: 1px solid var(--border);
            font-size: 11.5px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}

        .back-link {{
            color: var(--text-secondary);
            text-decoration: none;
            transition: color 0.15s;
        }}

        .back-link:hover {{
            color: var(--accent-green);
        }}
    </style>
</head>
<body>

<div class="login-card">
    <div class="logo-box">HF</div>
    <h1 class="login-title">Executive Management Hub</h1>
    <p class="login-subtitle">
        Restricted to executive administration. Enter passkey to decrypt analytics, revenue, and subscriber telemetry.
    </p>

    {error_block}

    <form id="admin-passkey-form" onsubmit="handlePasskeySubmit(event)">
        <div class="form-group">
            <label class="form-label" for="passkey-input">Executive Passkey</label>
            <div class="input-wrapper">
                <input 
                    type="password" 
                    id="passkey-input" 
                    class="form-input" 
                    placeholder="••••••••••••" 
                    required 
                    autocomplete="current-password"
                    autofocus
                >
                <button type="button" class="eye-toggle-btn" onclick="togglePasskeyVisibility()" title="Toggle visibility">
                    <svg id="eye-icon" width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path>
                        <circle cx="12" cy="12" r="3"></circle>
                    </svg>
                </button>
            </div>
        </div>

        <button type="submit" class="submit-btn" id="login-submit-btn">
            <span>Unlock Executive Hub</span>
            <span>→</span>
        </button>
    </form>

    <div class="login-footer">
        <span>🔒 7-Day Encrypted Session</span>
        <a href="/" class="back-link">Return to Customer Chat ↗</a>
    </div>
</div>

<script>
function togglePasskeyVisibility() {{
    const input = document.getElementById('passkey-input');
    if (input.type === 'password') {{
        input.type = 'text';
    }} else {{
        input.type = 'password';
    }}
}}

async function handlePasskeySubmit(e) {{
    e.preventDefault();
    const btn = document.getElementById('login-submit-btn');
    const input = document.getElementById('passkey-input');
    const passkey = input.value.trim();
    if (!passkey) return;

    btn.disabled = true;
    btn.innerHTML = '<span>Verifying...</span>';

    try {{
        const res = await fetch('/api/admin/login', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ passkey: passkey }})
        }});
        const data = await res.json();
        if (data.status === 'ok') {{
            btn.innerHTML = '<span>Access Granted ✓</span>';
            window.location.href = '/admin/dashboard';
        }} else {{
            btn.disabled = false;
            btn.innerHTML = '<span>Unlock Executive Hub</span><span>→</span>';
            alert(data.message || 'Invalid Executive Passkey');
            input.value = '';
            input.focus();
        }}
    }} catch (err) {{
        btn.disabled = false;
        btn.innerHTML = '<span>Unlock Executive Hub</span><span>→</span>';
        alert('Error connecting to server. Please try again.');
    }}
}}
</script>

</body>
</html>
"""

