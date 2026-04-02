import os
from dotenv import load_dotenv

load_dotenv()

# ─── Only these 2 stay as env vars on Railway ────────────────────────────────
SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# ─── Railway needs PORT as env var ───────────────────────────────────────────
PORT = int(os.getenv("PORT", 8000))

# ─── Globals (populated at startup by load_remote_config) ────────────────────
PLATFORM_API_KEY        = ""
PLATFORM_LLM_MODEL      = "openai/gpt-4o-mini"
PLATFORM_LLM_BASE_URL   = "https://openrouter.ai/api/v1"
EMBEDDING_API_KEY       = ""
EMBEDDING_MODEL         = "text-embedding-3-small"
EMBEDDING_BASE_URL      = ""
BROWSERBASE_API_KEY     = ""
BROWSERBASE_PROJECT_ID  = ""
GOOGLE_CLIENT_ID        = ""
GOOGLE_CLIENT_SECRET    = ""
APP_URL                 = ""
WEBHOOK_SECRET          = "changeme"
WORKER_INTERVAL_SECONDS = 300
RECENT_MESSAGES_LIMIT   = 12
MEMORY_RECALL_LIMIT     = 6
PROACTIVE_SILENCE_HOURS = 4


def load_remote_config():
    """Call once at startup — pulls everything from ai_config row id=1."""
    from supabase import create_client
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    resp = client.table("ai_config").select("*").eq("id", 1).single().execute()
    cfg = resp.data or {}

    global PLATFORM_API_KEY, PLATFORM_LLM_MODEL, PLATFORM_LLM_BASE_URL
    global EMBEDDING_API_KEY, EMBEDDING_MODEL, EMBEDDING_BASE_URL
    global BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID
    global GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    global APP_URL, WEBHOOK_SECRET
    global WORKER_INTERVAL_SECONDS, RECENT_MESSAGES_LIMIT
    global MEMORY_RECALL_LIMIT, PROACTIVE_SILENCE_HOURS

    PLATFORM_API_KEY        = cfg.get("platform_api_key", "")
    PLATFORM_LLM_MODEL      = cfg.get("platform_llm_model", "openai/gpt-4o-mini")
    PLATFORM_LLM_BASE_URL   = cfg.get("platform_llm_base_url", "https://openrouter.ai/api/v1")
    EMBEDDING_API_KEY       = cfg.get("embedding_api_key", "")
    EMBEDDING_MODEL         = cfg.get("embedding_model", "text-embedding-3-small")
    EMBEDDING_BASE_URL      = cfg.get("embedding_base_url", "")
    BROWSERBASE_API_KEY     = cfg.get("browserbase_api_key", "")
    BROWSERBASE_PROJECT_ID  = cfg.get("browserbase_project_id", "")
    GOOGLE_CLIENT_ID        = cfg.get("google_client_id", "")
    GOOGLE_CLIENT_SECRET    = cfg.get("google_client_secret", "")
    APP_URL                 = cfg.get("app_url", "")
    WEBHOOK_SECRET          = cfg.get("webhook_secret", "changeme")
    WORKER_INTERVAL_SECONDS = cfg.get("worker_interval_seconds", 300)
    RECENT_MESSAGES_LIMIT   = cfg.get("recent_messages_limit", 12)
    MEMORY_RECALL_LIMIT     = cfg.get("memory_recall_limit", 6)
    PROACTIVE_SILENCE_HOURS = cfg.get("proactive_silence_hours", 4)