"""
db.py — all Supabase read/write operations.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from supabase import create_client, Client
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY

# ── Single shared client ──────────────────────────────────────────────────────
_client: Client | None = None

def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _client


# ═══════════════════════════════════════════════════════════════════════════════
#  AI CONFIG  (your platform-level defaults — one row, id=1)
# ═══════════════════════════════════════════════════════════════════════════════

_ai_config_cache: dict | None = None

def get_ai_config() -> dict:
    global _ai_config_cache
    if _ai_config_cache is not None:
        return _ai_config_cache
    resp = (
        get_client()
        .table("ai_config")
        .select("*")
        .eq("id", 1)
        .single()
        .execute()
    )
    _ai_config_cache = resp.data or {}
    return _ai_config_cache

def invalidate_ai_config_cache() -> None:
    global _ai_config_cache
    _ai_config_cache = None


# ═══════════════════════════════════════════════════════════════════════════════
#  TENANTS  (one row per client you sell to)
# ═══════════════════════════════════════════════════════════════════════════════

_tenant_cache: dict = {}  # bot_token → tenant row

def get_tenant_by_bot_token(bot_token: str) -> dict | None:
    """Look up tenant by their Telegram bot token. Cached per process."""
    if bot_token in _tenant_cache:
        return _tenant_cache[bot_token]
     resp = (
        get_client()
        .table("tenants")
        .select("*")
        .eq("telegram_bot_token", bot_token)
        .eq("is_active", True)
        .maybe_single()
        .execute()
    )
    if resp.data:
        _tenant_cache[bot_token] = resp.data
    return resp.data if resp.data else None

def get_all_tenants() -> list[dict]:
    resp = get_client().table("tenants").select("*").eq("is_active", True).execute()
    return resp.data or []

def create_tenant(name: str, slug: str, bot_token: str,
                  plan: str = "trial") -> dict:
    resp = (
        get_client()
        .table("tenants")
        .insert({
            "name":               name,
            "slug":               slug,
            "telegram_bot_token": bot_token,
            "plan":               plan,
        })
        .execute()
    )
    return resp.data[0] if resp.data else {}

def update_tenant(tenant_id: str, **fields) -> None:
    get_client().table("tenants").update(fields).eq("id", tenant_id).execute()
    # Invalidate cache
    global _tenant_cache
    _tenant_cache = {}


# ═══════════════════════════════════════════════════════════════════════════════
#  USER CONFIG  (3-level priority: user → tenant → platform)
# ═══════════════════════════════════════════════════════════════════════════════

def get_user_config(user: dict, tenant: dict | None = None) -> dict:
    """
    Priority for every key:
      1. User's own value (in users table)
      2. Tenant's value (in tenants table)
      3. Platform default (in ai_config table)
      4. config.py env var fallback
    """
    from config import (
        PLATFORM_API_KEY, PLATFORM_LLM_MODEL, PLATFORM_LLM_BASE_URL,
        EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_BASE_URL,
    )
    ai  = get_ai_config()
    t   = tenant or {}

    platform_key = ai.get("platform_api_key") or PLATFORM_API_KEY

    return {
        # LLM — user → tenant → platform
        "llm_api_key":  (user.get("llm_api_key")
                         or t.get("llm_api_key")
                         or platform_key),
        "llm_model":    (user.get("llm_model")
                         or t.get("llm_model")
                         or ai.get("platform_llm_model")
                         or PLATFORM_LLM_MODEL),
        "llm_base_url": (user.get("llm_base_url")
                         or t.get("llm_base_url")
                         or ai.get("platform_llm_base_url")
                         or PLATFORM_LLM_BASE_URL),
        # Embeddings — always platform-level (tenants don't override this)
        "embedding_api_key":   (user.get("embedding_api_key")
                                or ai.get("embedding_api_key")
                                or EMBEDDING_API_KEY
                                or platform_key),
        "embedding_model":     (user.get("embedding_model")
                                or ai.get("embedding_model")
                                or EMBEDDING_MODEL),
        "embedding_base_url":  (user.get("embedding_base_url")
                                or ai.get("embedding_base_url")
                                or EMBEDDING_BASE_URL),
        # Google — tenant can have their own OAuth app, user has their own token
        "gmail_token":          user.get("gmail_token"),
        "google_client_id":     (t.get("google_client_id")
                                 or ai.get("google_client_id")),
        "google_client_secret": (t.get("google_client_secret")
                                 or ai.get("google_client_secret")),
        # Meta
        "tenant_id": t.get("id"),
        "timezone":  user.get("timezone", "UTC"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  USERS
# ═══════════════════════════════════════════════════════════════════════════════

def get_user_by_telegram_id(telegram_chat_id: int,
                             tenant_id: str | None = None) -> dict | None:
    q = (
        get_client()
        .table("users")
        .select("*")
        .eq("telegram_chat_id", telegram_chat_id)
    )
    if tenant_id:
        q = q.eq("tenant_id", tenant_id)
    resp = q.maybe_single().execute()
    return resp.data if resp.data else None

def get_all_active_users() -> list[dict]:
    resp = get_client().table("users").select("*").execute()
    return resp.data or []

def get_users_by_tenant(tenant_id: str) -> list[dict]:
    resp = (
        get_client()
        .table("users")
        .select("*")
        .eq("tenant_id", tenant_id)
        .execute()
    )
    return resp.data or []

def upsert_user(telegram_chat_id: int, **fields) -> dict:
    data = {"telegram_chat_id": telegram_chat_id, **fields}
    resp = (
        get_client()
        .table("users")
        .upsert(data, on_conflict="telegram_chat_id")
        .execute()
    )
    return resp.data[0] if resp.data else {}

def update_user_last_active(user_id: str) -> None:
    get_client().table("users").update(
        {"last_active_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", user_id).execute()


# ═══════════════════════════════════════════════════════════════════════════════
#  MESSAGES
# ═══════════════════════════════════════════════════════════════════════════════

def save_message(user_id: str, role: str, content: str,
                 embedding: list[float] | None = None,
                 tenant_id: str | None = None) -> None:
    row: dict[str, Any] = {
        "user_id":    user_id,
        "role":       role,
        "content":    content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if embedding:
        row["embedding"] = embedding
    if tenant_id:
        row["tenant_id"] = tenant_id
    get_client().table("messages").insert(row).execute()

def get_recent_messages(user_id: str, limit: int = 12) -> list[dict]:
    resp = (
        get_client()
        .table("messages")
        .select("role, content, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return list(reversed(resp.data or []))


# ═══════════════════════════════════════════════════════════════════════════════
#  MEMORIES
# ═══════════════════════════════════════════════════════════════════════════════

def save_memory(user_id: str, content: str,
                embedding: list[float],
                memory_type: str = "fact",
                importance: int = 5,
                tenant_id: str | None = None) -> None:
    row: dict[str, Any] = {
        "user_id":     user_id,
        "content":     content,
        "embedding":   embedding,
        "memory_type": memory_type,
        "importance":  importance,
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }
    if tenant_id:
        row["tenant_id"] = tenant_id
    get_client().table("memories").insert(row).execute()

def search_memories(user_id: str, query_embedding: list[float],
                    limit: int = 6) -> list[str]:
    resp = get_client().rpc("search_memories", {
        "p_user_id":   user_id,
        "p_embedding": query_embedding,
        "p_limit":     limit,
    }).execute()
    return [r["content"] for r in (resp.data or [])]

def get_all_memories(user_id: str) -> list[dict]:
    resp = (
        get_client()
        .table("memories")
        .select("content, memory_type, importance, created_at")
        .eq("user_id", user_id)
        .order("importance", desc=True)
        .limit(50)
        .execute()
    )
    return resp.data or []


# ═══════════════════════════════════════════════════════════════════════════════
#  TASKS
# ═══════════════════════════════════════════════════════════════════════════════

def save_task(user_id: str, description: str, task_type: str = "general",
              follow_up_at: str | None = None,
              tenant_id: str | None = None) -> str:
    row: dict[str, Any] = {
        "user_id":          user_id,
        "description":      description,
        "task_type":        task_type,
        "status":           "pending",
        "follow_up_needed": follow_up_at is not None,
        "follow_up_at":     follow_up_at,
        "created_at":       datetime.now(timezone.utc).isoformat(),
    }
    if tenant_id:
        row["tenant_id"] = tenant_id
    resp = get_client().table("tasks").insert(row).execute()
    return resp.data[0]["id"] if resp.data else ""

def complete_task(task_id: str, result: str) -> None:
    get_client().table("tasks").update({
        "status":       "completed",
        "result":       result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", task_id).execute()

def get_pending_followups(user_id: str) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        get_client()
        .table("tasks")
        .select("*")
        .eq("user_id", user_id)
        .eq("follow_up_needed", True)
        .eq("status", "pending")
        .lte("follow_up_at", now)
        .execute()
    )
    return resp.data or []


# ═══════════════════════════════════════════════════════════════════════════════
#  BEHAVIOUR STATE
# ═══════════════════════════════════════════════════════════════════════════════

def get_behaviour_state(user_id: str) -> dict:
    resp = (
        get_client()
        .table("behaviour_state")
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    if resp.data:
        return resp.data
    default = {
        "user_id":                user_id,
        "last_proactive_at":      None,
        "silence_hours":          0,
        "preferred_active_hours": "9-21",
        "daily_briefing_time":    "09:00",
        "last_briefing_at":       None,
    }
    get_client().table("behaviour_state").insert(default).execute()
    return default

def update_behaviour_state(user_id: str, **fields) -> None:
    get_client().table("behaviour_state").upsert(
        {"user_id": user_id, **fields},
        on_conflict="user_id"
    ).execute()


# ═══════════════════════════════════════════════════════════════════════════════
#  SCHEDULED JOBS
# ═══════════════════════════════════════════════════════════════════════════════

def get_due_scheduled_jobs() -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    resp = (
        get_client()
        .table("scheduled_jobs")
        .select("*")
        .eq("is_active", True)
        .lte("next_run_at", now)
        .execute()
    )
    return resp.data or []

def update_job_next_run(job_id: str, next_run_at: str) -> None:
    get_client().table("scheduled_jobs").update(
        {"next_run_at": next_run_at}
    ).eq("id", job_id).execute()


# ═══════════════════════════════════════════════════════════════════════════════
#  BROWSER SESSIONS
# ═══════════════════════════════════════════════════════════════════════════════

def save_pending_browser_session(user_id: str, session_id: str,
                                 instruction: str, url: str) -> None:
    get_client().table("browser_sessions").upsert({
        "user_id":     user_id,
        "session_id":  session_id,
        "instruction": instruction,
        "url":         url,
        "created_at":  datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id").execute()

def get_pending_browser_session(user_id: str) -> dict | None:
    resp = (
        get_client()
        .table("browser_sessions")
        .select("*")
        .eq("user_id", user_id)
        .maybe_single()
        .execute()
    )
    return resp.data if resp.data else None

def delete_pending_browser_session(user_id: str) -> None:
    get_client().table("browser_sessions").delete().eq("user_id", user_id).execute()


# ═══════════════════════════════════════════════════════════════════════════════
#  USAGE LOGGING  (for billing)
# ═══════════════════════════════════════════════════════════════════════════════

def log_usage(tenant_id: str, user_id: str, event_type: str) -> None:
    """
    Log a billable event. Call this whenever something happens you want to charge for.
    event_type examples: 'message', 'email_sent', 'browser_task', 'memory_saved'
    """
    try:
        get_client().table("usage_log").insert({
            "tenant_id":  tenant_id,
            "user_id":    user_id,
            "event_type": event_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception as e:
        print(f"[db] usage log error: {e}")

def get_tenant_usage(tenant_id: str, since_days: int = 30) -> dict:
    """Get usage counts per event type for a tenant over the last N days."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
    resp = (
        get_client()
        .table("usage_log")
        .select("event_type")
        .eq("tenant_id", tenant_id)
        .gte("created_at", since)
        .execute()
    )
    counts: dict[str, int] = {}
    for row in (resp.data or []):
        e = row["event_type"]
        counts[e] = counts.get(e, 0) + 1
    return counts