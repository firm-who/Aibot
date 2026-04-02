"""
auth.py — Google OAuth2 flow for Gmail + Drive + Sheets + Docs + Business Profile.

Google credentials (client_id, client_secret) are read from the
`ai_config` table in Supabase (row id=1), NOT from Render env vars.
"""

from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/auth")

SCOPES = [
    # Identity
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    # Gmail
    "https://www.googleapis.com/auth/gmail.modify",
    # Drive (includes Docs & Sheets file access)
    "https://www.googleapis.com/auth/drive",
    # Sheets (explicit)
    "https://www.googleapis.com/auth/spreadsheets",
    # Docs (explicit)
    "https://www.googleapis.com/auth/documents",
    # Calendar (future use)
    "https://www.googleapis.com/auth/calendar",
    # Google Business Profile
    "https://www.googleapis.com/auth/business.manage",
]


def _get_google_creds() -> tuple[str, str]:
    from db import get_ai_config
    cfg = get_ai_config()
    client_id     = cfg.get("google_client_id", "").strip()
    client_secret = cfg.get("google_client_secret", "").strip()
    if not client_id or not client_secret:
        raise ValueError(
            "Google OAuth not configured. "
            "Add google_client_id and google_client_secret to the ai_config table in Supabase."
        )
    return client_id, client_secret


def _make_flow(redirect_uri: str):
    from google_auth_oauthlib.flow import Flow
    client_id, client_secret = _get_google_creds()
    client_config = {
        "web": {
            "client_id":     client_id,
            "client_secret": client_secret,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }
    flow = Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = redirect_uri
    return flow, client_id, client_secret


@router.get("/google")
async def google_login(request: Request, telegram_chat_id: str):
    from config import APP_URL
    try:
        redirect_uri = f"{APP_URL}/auth/google/callback"
        flow, _, _ = _make_flow(redirect_uri)
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            state=str(telegram_chat_id),
        )
        return RedirectResponse(auth_url)
    except ValueError as e:
        return HTMLResponse(_error_page(str(e)), status_code=500)
    except Exception as e:
        return HTMLResponse(_error_page(str(e)), status_code=500)


@router.get("/google/callback")
async def google_callback(request: Request, code: str, state: str):
    from config import APP_URL
    from db import get_client
    try:
        redirect_uri = f"{APP_URL}/auth/google/callback"
        flow, client_id, client_secret = _make_flow(redirect_uri)
        flow.fetch_token(code=code)
        creds = flow.credentials

        gmail_token = {
            "access_token":  creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri":     creds.token_uri,
            "client_id":     client_id,
            "client_secret": client_secret,
            "scopes":        list(creds.scopes or SCOPES),
        }

        telegram_chat_id = int(state)
        get_client().table("users").update(
            {"gmail_token": gmail_token}
        ).eq("telegram_chat_id", telegram_chat_id).execute()

        return HTMLResponse(_success_page())
    except Exception as e:
        return HTMLResponse(_error_page(str(e)), status_code=400)


def _success_page() -> str:
    return """
    <html>
    <head>
      <meta charset="UTF-8"/>
      <title>Google Connected</title>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet"/>
      <style>
        body { margin:0;padding:0;background:#0c0c0e;color:#e8e8f0;font-family:'IBM Plex Mono',monospace;
               display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center; }
        .box { padding:48px 40px;border:1px solid #1a3a22;border-radius:12px;background:#0a140a;max-width:420px; }
        .icon { font-size:44px;margin-bottom:20px; }
        h2 { color:#4ade80;font-size:18px;margin-bottom:10px;letter-spacing:-0.02em; }
        p  { color:#55556a;font-size:12px;line-height:1.7; }
        ul { text-align:left;color:#4ade80;font-size:11px;line-height:2;padding-left:20px; }
      </style>
    </head>
    <body>
      <div class="box">
        <div class="icon">✓</div>
        <h2>Google connected</h2>
        <p>Access granted for:</p>
        <ul>
          <li>Gmail</li>
          <li>Google Drive</li>
          <li>Google Docs &amp; Sheets</li>
          <li>Google Calendar</li>
          <li>Business Profile</li>
        </ul>
        <p style="margin-top:16px">You can close this tab.<br/>Go back to Telegram — your agent is ready.</p>
        <script>setTimeout(() => window.close(), 4000);</script>
      </div>
    </body>
    </html>
    """


def _error_page(msg: str) -> str:
    return f"""
    <html>
    <head>
      <meta charset="UTF-8"/><title>OAuth Error</title>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet"/>
      <style>
        body {{ margin:0;padding:0;background:#0c0c0e;color:#e8e8f0;font-family:'IBM Plex Mono',monospace;
                display:flex;align-items:center;justify-content:center;min-height:100vh;text-align:center; }}
        .box {{ padding:48px 40px;border:1px solid #3a1515;border-radius:12px;background:#110909;max-width:420px; }}
        .icon {{ font-size:44px;margin-bottom:20px; }}
        h2 {{ color:#f87171;font-size:18px;margin-bottom:10px; }}
        p  {{ color:#55556a;font-size:11px;line-height:1.7;word-break:break-word; }}
      </style>
    </head>
    <body>
      <div class="box">
        <div class="icon">✗</div>
        <h2>OAuth failed</h2>
        <p>{msg}</p>
      </div>
    </body>
    </html>
    """