"""
tools/telegram_sender.py — Send messages and media to Telegram.

Each user has their own bot token stored in Supabase.
We use httpx directly (no library dependency) for simplicity.
"""

from __future__ import annotations
import base64
import httpx


async def send_message(bot_token: str, chat_id: int, text: str,
                       parse_mode: str = "HTML") -> bool:
    """Send a text message to a Telegram chat."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={
                "chat_id":    chat_id,
                "text":       text,
                "parse_mode": parse_mode,
            })
            return resp.status_code == 200
    except Exception as e:
        print(f"[telegram] send_message error: {e}")
        return False


async def send_photo_base64(bot_token: str, chat_id: int,
                            photo_base64: str, caption: str = "") -> bool:
    """Send a screenshot (base64 PNG) to Telegram as a photo message."""
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    try:
        photo_bytes = base64.b64decode(photo_base64)
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, data={
                "chat_id": str(chat_id),
                "caption": caption,
            }, files={"photo": ("screenshot.png", photo_bytes, "image/png")})
            return resp.status_code == 200
    except Exception as e:
        print(f"[telegram] send_photo error: {e}")
        return False


async def send_typing_action(bot_token: str, chat_id: int) -> None:
    """Show 'typing...' indicator while agent is working."""
    url = f"https://api.telegram.org/bot{bot_token}/sendChatAction"
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "action":  "typing",
            })
    except Exception:
        pass


def send_message_sync(bot_token: str, chat_id: int, text: str) -> bool:
    """Synchronous version for use in background worker (not async context)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = httpx.post(url, json={
            "chat_id":    chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[telegram] send_message_sync error: {e}")
        return False


async def register_webhook(bot_token: str, webhook_url: str) -> bool:
    """Register the webhook URL with Telegram. Call this once on deployment."""
    url = f"https://api.telegram.org/bot{bot_token}/setWebhook"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"url": webhook_url})
            data = resp.json()
            print(f"[telegram] setWebhook response: {data}")
            return data.get("ok", False)
    except Exception as e:
        print(f"[telegram] register_webhook error: {e}")
        return False
