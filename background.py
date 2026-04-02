"""
background.py — Proactive background worker.

Runs every 5 minutes via APScheduler. Loops through ALL users and decides
whether to send them a proactive message. This is what makes the agent feel
like a real friend — it reaches out without being asked.

Proactive triggers (in priority order):
  1. Scheduled daily briefing (good morning message)
  2. Pending task follow-ups (e.g. "client hasn't replied in 24h")
  3. Long silence check-in (user hasn't messaged in X hours)
  4. Scheduled custom jobs (user-defined recurring messages)
"""

from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta

import db
import llm as llm_module
from tools.telegram_sender import send_message_sync
from config import PROACTIVE_SILENCE_HOURS


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN WORKER ENTRY POINT  (called by APScheduler every 5 min)
# ═══════════════════════════════════════════════════════════════════════════════

def run_proactive_worker() -> None:
    """
    Synchronous wrapper — APScheduler runs this in a thread.
    We use asyncio.run() to run the async logic inside it.
    """
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

    # Skip users with no bot token or LLM key (not fully set up)
    if not bot_token or not user_config.get("llm_api_key"):
        return

    state = db.get_behaviour_state(user_id)
    now   = datetime.now(timezone.utc)

    # ── Check 1: Daily briefing ──────────────────────────────────────────────
    if await _should_send_briefing(state, user_config, now):
        await _send_daily_briefing(user, user_config, bot_token, chat_id, now)
        return  # Only one proactive message per cycle

    # ── Check 2: Task follow-ups ─────────────────────────────────────────────
    follow_ups = db.get_pending_followups(user_id)
    if follow_ups:
        await _send_followup_message(
            follow_ups[0], user, user_config, bot_token, chat_id, now
        )
        return

    # ── Check 3: Silence check-in ────────────────────────────────────────────
    if await _should_check_in(user, state, now):
        await _send_checkin_message(user, user_config, bot_token, chat_id, now)
        return

    # ── Check 4: Scheduled jobs ──────────────────────────────────────────────
    due_jobs = db.get_due_scheduled_jobs()
    user_jobs = [j for j in due_jobs if j.get("user_id") == user_id]
    if user_jobs:
        await _run_scheduled_job(user_jobs[0], user, user_config, bot_token, chat_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  PROACTIVE ACTIONS
# ═══════════════════════════════════════════════════════════════════════════════

async def _send_daily_briefing(user: dict, user_config: dict,
                                bot_token: str, chat_id: int,
                                now: datetime) -> None:
    """Send a personalised morning briefing."""
    print(f"[background] sending daily briefing to {chat_id}")

    memories      = db.get_all_memories(user["id"])
    pending_tasks = db.get_pending_followups(user["id"])
    user_name     = user.get("name", "")

    message = llm_module.generate_daily_briefing(
        memories=memories,
        pending_tasks=pending_tasks,
        user_config=user_config,
        user_name=user_name,
    )

    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.update_behaviour_state(
            user["id"],
            last_briefing_at=now.isoformat(),
            last_proactive_at=now.isoformat(),
        )


async def _send_followup_message(task: dict, user: dict, user_config: dict,
                                  bot_token: str, chat_id: int,
                                  now: datetime) -> None:
    """
    Send a proactive follow-up about a pending task.
    Example: "Hey, it's been 4 hours since we emailed Tanaka-san — want me to check?"
    """
    print(f"[background] sending follow-up for task {task['id']}")

    # Get memories relevant to this task for personalisation
    from memory import recall_relevant_memories
    relevant = recall_relevant_memories(
        user["id"], task["description"], user_config, limit=3
    )
    context = task["description"]
    if relevant:
        context += "\n\nRelated context:\n" + "\n".join(relevant)

    # Calculate how long ago the task was created
    created_at = datetime.fromisoformat(
        task["created_at"].replace("Z", "+00:00")
    )
    hours_ago = int((now - created_at).total_seconds() / 3600)

    reason = (
        f"Task created {hours_ago} hours ago needs follow-up: {task['description']}"
    )

    message = llm_module.generate_proactive_message(
        reason=reason,
        context=context,
        user_config=user_config,
        user_name=user.get("name", ""),
    )

    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        # Mark this task as follow-up sent (prevent re-sending)
        db.get_client().table("tasks").update({
            "follow_up_needed": False,
            "status": "awaiting_response",
        }).eq("id", task["id"]).execute()

        db.update_behaviour_state(
            user["id"],
            last_proactive_at=now.isoformat(),
        )


async def _send_checkin_message(user: dict, user_config: dict,
                                 bot_token: str, chat_id: int,
                                 now: datetime) -> None:
    """
    Send a gentle check-in when user has been silent for a while.
    Uses memories to make it personal and relevant.
    """
    print(f"[background] sending check-in to {chat_id}")

    last_active = user.get("last_active_at")
    if last_active:
        last_dt   = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
        hours_ago = int((now - last_dt).total_seconds() / 3600)
    else:
        hours_ago = 0

    # Get recent memories for context
    from memory import recall_relevant_memories
    recent_context = recall_relevant_memories(
        user["id"], "recent work tasks business", user_config, limit=4
    )
    context = "\n".join(recent_context) if recent_context else "Not much context yet."

    reason = f"User has been quiet for {hours_ago} hours. Check in warmly."

    message = llm_module.generate_proactive_message(
        reason=reason,
        context=context,
        user_config=user_config,
        user_name=user.get("name", ""),
    )

    sent = send_message_sync(bot_token, chat_id, message)
    if sent:
        db.update_behaviour_state(
            user["id"],
            last_proactive_at=now.isoformat(),
        )


async def _run_scheduled_job(job: dict, user: dict, user_config: dict,
                              bot_token: str, chat_id: int) -> None:
    """Run a user-defined scheduled job (e.g. weekly report, daily reminder)."""
    import json
    from datetime import timedelta

    payload = job.get("payload", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}

    message_template = payload.get("message", "Scheduled check-in from your agent.")

    # Personalise using memories
    from memory import recall_relevant_memories
    context = recall_relevant_memories(
        user["id"], message_template, user_config, limit=3
    )
    if context:
        reason = f"Scheduled job: {message_template}"
        message = llm_module.generate_proactive_message(
            reason=reason,
            context="\n".join(context),
            user_config=user_config,
        )
    else:
        message = message_template

    send_message_sync(bot_token, chat_id, message)

    # Update next run time based on cron expression (simplified: daily = +24h)
    next_run = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    db.update_job_next_run(job["id"], next_run)


# ═══════════════════════════════════════════════════════════════════════════════
#  DECISION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

async def _should_send_briefing(state: dict, user_config: dict,
                                 now: datetime) -> bool:
    """Should we send the daily morning briefing right now?"""
    briefing_time = state.get("daily_briefing_time", "09:00")
    last_briefing = state.get("last_briefing_at")

    try:
        hour, minute = map(int, briefing_time.split(":"))
    except Exception:
        hour, minute = 9, 0

    # Is it the right time? (within a 10-minute window)
    current_hour   = now.hour
    current_minute = now.minute
    in_window = (current_hour == hour and abs(current_minute - minute) <= 10)
    if not in_window:
        return False

    # Was briefing already sent today?
    if last_briefing:
        last_dt = datetime.fromisoformat(last_briefing.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 20:  # Less than 20 hours ago
            return False

    return True


async def _should_check_in(user: dict, state: dict, now: datetime) -> bool:
    """Should we send a silence check-in?"""
    # Don't check in if we proactively messaged recently (within 2 hours)
    last_proactive = state.get("last_proactive_at")
    if last_proactive:
        last_dt = datetime.fromisoformat(last_proactive.replace("Z", "+00:00"))
        if (now - last_dt).total_seconds() < 3600 * 2:
            return False

    # Check if user has been silent long enough
    last_active = user.get("last_active_at")
    if not last_active:
        return False

    last_dt   = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
    hours_ago = (now - last_dt).total_seconds() / 3600

    if hours_ago < PROACTIVE_SILENCE_HOURS:
        return False

    # Respect preferred hours (don't message at 3am)
    preferred = state.get("preferred_active_hours", "9-21")
    try:
        start_h, end_h = map(int, preferred.split("-"))
        if not (start_h <= now.hour <= end_h):
            return False
    except Exception:
        pass

    return True
