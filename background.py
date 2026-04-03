"""
background.py — Proactive background worker.

Proactive triggers (in priority order):
  1. Daily briefing (morning)
  2. Evening news push (niche/interest news via web search)
  3. Get-to-know-you question (if user profile is sparse)
  4. Task follow-ups
  5. Silence check-in
  6. Scheduled custom jobs
"""

from __future__ import annotations
import asyncio
import random
from datetime import datetime, timezone, timedelta

import db
import llm as llm_module
from telegram_sender import send_message_sync
from config import PROACTIVE_SILENCE_HOURS


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN WORKER
# ═══════════════════════════════════════════════════════════════════════════════

def run_proactive_worker() -> None:
    try:
        asyncio.run(_async_worker())
    except Exception as e:
        print(f"[background] worker error: {e}")


async def _async_worker() -> None:
    users = db.get_all_active_users()
    print(f"[background] checking {len(users)} users")
    for user in users:
        try:
            await _process_user(user)
        except Exception as e:
            print(f"[background] error for user {user.get('id')}: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
#  PER-USER PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

async def _process_user(user: dict) -> None:
    user_id     = user["id"]
    chat_id     = user.get("telegram_chat_id")
    bot_token   = user.get("telegram_bot_token", "")
    user_config = db.get_user_config(user)

    if not bot_token or not user_config.get("llm_api_key"):
        return

    state = db.get_behaviour_state(user_id)
    now   = datetime.now(timezone.utc)

    # 1. Morning briefing (9am)
    if await _should_send_briefing(state, now):
        await _send_daily_briefing(user, user_config, bot_token, chat_id, now)
        return

    # 2. Evening news push (6pm)
    if await _should_send_news(state, now):
        await _send_news_message(user, user_config, bot_token, chat_id, now)
        return

    # 3. Get-to-know-you question (if profile is sparse, once per day max)
    if await _should_ask_profile_question(user, state, now):
        await _send_profile_question(user, user_config, bot_token, chat_id, now)
        return

    # 4. Task follow-ups
    follow_ups = db.get_pending_followups(user_id)
    if follow_ups:
        await _send_followup_message(follow_ups[0], user, user_config, bot_token, chat_id, now)
        return

    # 5. Silence check-in
    if await _should_check_in(user, state, now):
        await _send_checkin_message(user, user_config, bot_token, chat_id, now)
        return

    # 6. Scheduled jobs
    due_jobs  = db.get_due_scheduled_jobs()
    user_jobs = [j for j in due_jobs if j.get("user_id") == user_id]
    if user_jobs:
        await _run_scheduled_job(user_jobs[0], user, user_config, bot_token, chat_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  NEWS PUSH  (new)
# ═══════════════════════════════════════════════════════════════════════════════

async def _send_news_message(user: dict, user_config: dict,
                              bot_token: str, chat_id: int,
                              now: datetime) -> None:
    print(f"[background] sending news to {chat_id}")
    from search import web_search
    from memory import recall_relevant_memories

    # Build search query from user memories
    memories = recall_relevant_memories(user["id"], "business industry niche work", user_config, limit=5)
    context  = "\n".join(memories) if memories else ""

    # Ask LLM what to search for based on user context
    search_query = llm_module.call_llm_raw(
        messages=[{"role": "user", "content": f"""Based on what you know about this user, suggest ONE specific news search query (5 words max) that would be most relevant and interesting to them today.

User context:
{context or "No context yet — use general business/entrepreneurship news"}

Reply with ONLY the search query, nothing else."""}],
        user_config=user_config,
        max_tokens=20,
    ).strip().strip('"')

    if not search_query:
        search_query = "startup business news today"

    # Search the web
    results = web_search(query=search_query, max_results=3)

    # Generate a punchy human message about it
    message = llm_module.call_llm_raw(
        messages=[{"role": "user", "content": f"""You're a sharp, switched-on assistant texting your friend who runs a business.

You just found this news: 
{results}

Write a short, direct Telegram message sharing the most interesting thing you found.
- Sound like a real person texting, not a newsletter
- Lead with the hook — what's actually interesting
- Max 4 sentences
- End with one sharp question or observation
- No emojis unless it fits naturally
- Don't say "I found" or "Here's a news" — just dive in

User's context: {context or "entrepreneur, business owner"}"""}],
        user_config=user_config,
        max_tokens=200,
    ).strip()

    if not message:
        return

    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.update_behaviour_state(
            user["id"],
            last_proactive_at=now.isoformat(),
            last_news_at=now.isoformat(),
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  GET-TO-KNOW-YOU QUESTIONS  (new)
# ═══════════════════════════════════════════════════════════════════════════════

_PROFILE_QUESTIONS = [
    "What kind of business are you running right now?",
    "What's the biggest thing on your plate this week?",
    "What industry are you in? I want to make sure I'm actually useful to you.",
    "What's one thing you wish you had more help with day-to-day?",
    "Who are your main clients or customers?",
    "What tools do you use most for work?",
    "What's your main goal for this month?",
    "What's been your biggest win recently?",
]

async def _send_profile_question(user: dict, user_config: dict,
                                  bot_token: str, chat_id: int,
                                  now: datetime) -> None:
    print(f"[background] sending profile question to {chat_id}")

    memories = db.get_all_memories(user["id"])
    asked_before = [m["content"] for m in memories if m.get("memory_type") == "profile"]

    # Pick a question not yet answered
    question = None
    for q in random.sample(_PROFILE_QUESTIONS, len(_PROFILE_QUESTIONS)):
        if not any(q[:20].lower() in m.lower() for m in asked_before):
            question = q
            break

    if not question:
        return  # All questions asked already

    sent = send_message_sync(bot_token, chat_id, question)
    if sent:
        db.update_behaviour_state(
            user["id"],
            last_proactive_at=now.isoformat(),
            last_question_at=now.isoformat(),
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  EXISTING ACTIONS (unchanged logic, cleaned up)
# ═══════════════════════════════════════════════════════════════════════════════

async def _send_daily_briefing(user: dict, user_config: dict,
                                bot_token: str, chat_id: int,
                                now: datetime) -> None:
    print(f"[background] sending daily briefing to {chat_id}")
    memories      = db.get_all_memories(user["id"])
    pending_tasks = db.get_pending_followups(user["id"])
    message = llm_module.generate_daily_briefing(
        memories=memories,
        pending_tasks=pending_tasks,
        user_config=user_config,
        user_name=user.get("name", ""),
    )
    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.update_behaviour_state(user["id"],
            last_briefing_at=now.isoformat(),
            last_proactive_at=now.isoformat())


async def _send_followup_message(task: dict, user: dict, user_config: dict,
                                  bot_token: str, chat_id: int,
                                  now: datetime) -> None:
    print(f"[background] sending follow-up for task {task['id']}")
    from memory import recall_relevant_memories
    relevant  = recall_relevant_memories(user["id"], task["description"], user_config, limit=3)
    context   = task["description"] + ("\n\n" + "\n".join(relevant) if relevant else "")
    created_at = datetime.fromisoformat(task["created_at"].replace("Z", "+00:00"))
    hours_ago  = int((now - created_at).total_seconds() / 3600)
    message = llm_module.generate_proactive_message(
        reason=f"Task created {hours_ago}h ago needs follow-up: {task['description']}",
        context=context, user_config=user_config, user_name=user.get("name", ""),
    )
    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.get_client().table("tasks").update({
            "follow_up_needed": False, "status": "awaiting_response",
        }).eq("id", task["id"]).execute()
        db.update_behaviour_state(user["id"], last_proactive_at=now.isoformat())


async def _send_checkin_message(user: dict, user_config: dict,
                                 bot_token: str, chat_id: int,
                                 now: datetime) -> None:
    print(f"[background] sending check-in to {chat_id}")
    from memory import recall_relevant_memories
    last_active = user.get("last_active_at")
    hours_ago   = int((now - datetime.fromisoformat(last_active.replace("Z", "+00:00"))).total_seconds() / 3600) if last_active else 0
    context     = "\n".join(recall_relevant_memories(user["id"], "recent work tasks", user_config, limit=4)) or "No context yet."
    message = llm_module.generate_proactive_message(
        reason=f"User quiet for {hours_ago}h. Check in naturally.",
        context=context, user_config=user_config, user_name=user.get("name", ""),
    )
    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.update_behaviour_state(user["id"], last_proactive_at=now.isoformat())


async def _run_scheduled_job(job: dict, user: dict, user_config: dict,
                              bot_token: str, chat_id: int) -> None:
    import json
    payload  = job.get("payload", {})
    if isinstance(payload, str):
        try: payload = json.loads(payload)
        except: payload = {}
    message_template = payload.get("message", "Scheduled check-in.")
    from memory import recall_relevant_memories
    context = recall_relevant_memories(user["id"], message_template, user_config, limit=3)
    message = llm_module.generate_proactive_message(
        reason=f"Scheduled: {message_template}",
        context="\n".join(context) if context else "",
        user_config=user_config,
    ) if context else message_template
    send_message_sync(bot_token, chat_id, message)
    db.update_job_next_run(job["id"], (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat())


# ═══════════════════════════════════════════════════════════════════════════════
#  DECISION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _should_send_briefing(state: dict, now: datetime) -> bool:
    briefing_time = state.get("daily_briefing_time", "09:00")
    last_briefing = state.get("last_briefing_at")
    try:
        hour, minute = map(int, briefing_time.split(":"))
    except:
        hour, minute = 9, 0
    if not (now.hour == hour and abs(now.minute - minute) <= 10):
        return False
    if last_briefing:
        last_dt = datetime.fromisoformat(last_briefing.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 20:
            return False
    return True


async def _should_send_news(state: dict, now: datetime) -> bool:
    """Send news at 6pm local (UTC for now), once per day."""
    if not (now.hour == 18 and now.minute <= 10):
        return False
    last_news = state.get("last_news_at")
    if last_news:
        last_dt = datetime.fromisoformat(last_news.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 20:
            return False
    return True


async def _should_ask_profile_question(user: dict, state: dict, now: datetime) -> bool:
    """Ask a profile question once per day, only if user has few memories."""
    memories = db.get_all_memories(user["id"])
    if len(memories) >= 8:
        return False  # Know enough already
    last_question = state.get("last_question_at")
    if last_question:
        last_dt = datetime.fromisoformat(last_question.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 24:
            return False
    # Only ask during active hours
    preferred = state.get("preferred_active_hours", "9-21")
    try:
        start_h, end_h = map(int, preferred.split("-"))
        if not (start_h <= now.hour <= end_h):
            return False
    except:
        pass
    return True


async def _should_check_in(user: dict, state: dict, now: datetime) -> bool:
    last_proactive = state.get("last_proactive_at")
    if last_proactive:
        last_dt = datetime.fromisoformat(last_proactive.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 2:
            return False
    last_active = user.get("last_active_at")
    if not last_active:
        return False
    hours_ago = (now - datetime.fromisoformat(last_active.replace("Z", "+00:00"))).total_seconds() / 3600
    if hours_ago < PROACTIVE_SILENCE_HOURS:
        return False
    preferred = state.get("preferred_active_hours", "9-21")
    try:
        start_h, end_h = map(int, preferred.split("-"))
        if not (start_h <= now.hour <= end_h):
            return False
    except:
        pass
    return True
