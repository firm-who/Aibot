"""
webhook.py — Handles all incoming Telegram messages.

Multi-tenant flow:
  1. Bot token in URL → look up tenant
  2. Chat ID → look up or create user (scoped to tenant)
  3. Run agent with tenant + user config
"""

from __future__ import annotations
import asyncio
from fastapi import APIRouter, Request, HTTPException
import db
import memory as mem
import llm as llm_module
from tools.executor import execute_tool
from tools.telegram_sender import send_message, send_typing_action, send_photo_base64
from config import RECENT_MESSAGES_LIMIT, APP_URL

router = APIRouter()

_YES = {"yes", "ok", "okay", "sure", "go", "confirm", "submit",
        "proceed", "do it", "yep", "yeah", "y"}
_NO  = {"no", "cancel", "stop", "abort", "nope", "n", "don't", "dont"}

_GMAIL_INTENTS = {
    "connect gmail", "link gmail", "gmail connect", "gmail access",
    "connect my gmail", "link my gmail", "connect email", "link email",
    "setup gmail", "set up gmail", "authorize gmail", "gmail auth",
    "connect google", "link google",
}


@router.post("/webhook/{bot_token}")
async def telegram_webhook(bot_token: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = body.get("message") or body.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]
    text    = message.get("text", "").strip()
    if not text:
        return {"ok": True}

    # ── 1. Look up tenant by bot token ───────────────────────────────────────
    tenant = db.get_tenant_by_bot_token(bot_token)
    if not tenant:
        # Unknown bot token — could be a new tenant being set up
        # Allow it but with no tenant_id (fallback mode)
        tenant = {}

    tenant_id = tenant.get("id")

    # Check tenant is active and not expired
    if tenant and not tenant.get("is_active", True):
        await send_message(bot_token, chat_id,
            "This service is currently inactive. Please contact support.")
        return {"ok": True}

    # ── 2. Look up or create user (scoped to tenant) ──────────────────────────
    user = db.get_user_by_telegram_id(chat_id, tenant_id)

   if not user:
    chat_id_display = chat_id  # Show it to them
    await send_message(bot_token, chat_id,
        f"You are not authorized.\n"
        f"(Your ID: {chat_id})\n"
        f"Contact your administrator.")

    # Keep bot token fresh
    if user.get("telegram_bot_token") != bot_token:
        db.upsert_user(chat_id, telegram_bot_token=bot_token)
        user["telegram_bot_token"] = bot_token

    db.update_user_last_active(user["id"])

    # ── 3. Log usage ──────────────────────────────────────────────────────────
    if tenant_id:
        db.log_usage(tenant_id, user["id"], "message")

    # ── 4. Gmail connect intent ───────────────────────────────────────────────
    text_lower = text.lower().strip()
    if _is_gmail_intent(text_lower):
        asyncio.create_task(_send_gmail_link(bot_token, chat_id, user, tenant))
        return {"ok": True}

    # ── 5. Build user config (3-level: user → tenant → platform) ─────────────
    user_config = db.get_user_config(user, tenant)
    if not user_config.get("llm_api_key"):
        await send_message(
            bot_token, chat_id,
            "⚙️ This agent isn't configured yet. Please contact the admin."
        )
        return {"ok": True}

    # ── 6. Browser confirmation check ────────────────────────────────────────
    pending = db.get_pending_browser_session(user["id"])
    if pending:
        word = text_lower.rstrip("!")
        if word in _YES:
            asyncio.create_task(
                _handle_browser_confirm(bot_token, chat_id, user, user_config, pending)
            )
            return {"ok": True}
        elif word in _NO:
            asyncio.create_task(
                _handle_browser_cancel(bot_token, chat_id, user, pending)
            )
            return {"ok": True}

    # ── 7. Normal agent loop ──────────────────────────────────────────────────
    asyncio.create_task(send_typing_action(bot_token, chat_id))
    reply = await run_agent(text, user, user_config, chat_id, bot_token)
    await send_message(bot_token, chat_id, reply)

    asyncio.create_task(
        post_conversation_tasks(text, reply, user, user_config, tenant_id)
    )
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
#  GMAIL LINK
# ═══════════════════════════════════════════════════════════════════════════════

def _is_gmail_intent(text: str) -> bool:
    for phrase in _GMAIL_INTENTS:
        if phrase in text:
            return True
    words = set(text.split())
    if "gmail" in words and any(w in words for w in ("connect","link","setup","auth","authorize","enable")):
        return True
    return False


async def _send_gmail_link(bot_token: str, chat_id: int,
                           user: dict, tenant: dict) -> None:
    try:
        # Use tenant's Google creds if they have their own, else platform
        google_client_id = (tenant.get("google_client_id")
                            or db.get_ai_config().get("google_client_id"))
        if not google_client_id:
            await send_message(bot_token, chat_id,
                "⚠️ Gmail integration isn't configured yet.")
            return

        oauth_url = f"{APP_URL}/auth/google?telegram_chat_id={chat_id}"
        await send_message(
            bot_token, chat_id,
            "📧 *Connect Gmail*\n\n"
            "Click the link below to authorise Google access "
            "(Gmail, Drive, Docs, Sheets, Calendar, Business Profile).\n\n"
            f"🔗 {oauth_url}\n\n"
            "_This link is personal — don't share it._"
        )
    except Exception as e:
        print(f"[webhook] _send_gmail_link error: {e}")
        await send_message(bot_token, chat_id,
            "Something went wrong generating your link. Please try again.")


# ═══════════════════════════════════════════════════════════════════════════════
#  BROWSER HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _handle_browser_confirm(bot_token, chat_id, user, user_config, pending):
    from tools.browser import submit_form
    await send_message(bot_token, chat_id, "✓ Submitting now...")
    result = await submit_form(pending["session_id"])
    if result.get("screenshot_base64"):
        caption = ("✓ Done! " if result["success"] else "✗ ") + result["result"]
        await send_photo_base64(bot_token, chat_id, result["screenshot_base64"], caption)
    else:
        await send_message(bot_token, chat_id,
            ("✓ " if result["success"] else "✗ ") + result["result"])
    db.delete_pending_browser_session(user["id"])


async def _handle_browser_cancel(bot_token, chat_id, user, pending):
    from tools.browser import cancel_session
    await cancel_session(pending["session_id"])
    db.delete_pending_browser_session(user["id"])
    await send_message(bot_token, chat_id, "Cancelled. Nothing was submitted.")


# ═══════════════════════════════════════════════════════════════════════════════
#  AGENT LOOP
# ═══════════════════════════════════════════════════════════════════════════════

async def run_agent(user_message: str, user: dict, user_config: dict,
                    telegram_chat_id: int, bot_token: str) -> str:
    memory_context = mem.build_memory_context(user["id"], user_message, user_config)
    recent_msgs    = db.get_recent_messages(user["id"], RECENT_MESSAGES_LIMIT)
    conversation   = recent_msgs + [{"role": "user", "content": user_message}]

    result = {}
    for _ in range(5):
        result = llm_module.call_llm(
            user=user,
            user_config=user_config,
            conversation_history=conversation,
            memory_context=memory_context,
        )

        if result["finish_reason"] == "stop" or not result["tool_calls"]:
            return result["text"] or "I'm not sure how to respond to that."

        tool_results = []
        for tc in result["tool_calls"]:
            tool_output = await execute_tool(
                tool_name=tc["name"],
                arguments=tc["arguments"],
                user=user,
                user_config=user_config,
                telegram_chat_id=telegram_chat_id,
                bot_token=bot_token,
            )
            tool_results.append({
                "tool_call_id": tc["id"],
                "name":         tc["name"],
                "content":      tool_output,
            })

        assistant_msg: dict = {"role": "assistant", "content": result["text"] or ""}
        if result["tool_calls"]:
            assistant_msg["tool_calls"] = [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": str(tc["arguments"])}}
                for tc in result["tool_calls"]
            ]
        conversation.append(assistant_msg)
        for tr in tool_results:
            conversation.append({
                "role": "tool", "tool_call_id": tr["tool_call_id"],
                "name": tr["name"], "content": tr["content"],
            })

    return result.get("text") or "Task completed."


async def post_conversation_tasks(user_message: str, assistant_reply: str,
                                   user: dict, user_config: dict,
                                   tenant_id: str | None = None) -> None:
    try:
        user_embedding = mem.embed_text(user_message, user_config)
        db.save_message(user["id"], "user", user_message, user_embedding, tenant_id)

        assistant_embedding = mem.embed_text(assistant_reply, user_config)
        db.save_message(user["id"], "assistant", assistant_reply, assistant_embedding, tenant_id)

        mem.extract_and_save_facts(user["id"], user_message, assistant_reply, user_config)
    except Exception as e:
        print(f"[webhook] post_conversation_tasks error: {e}")