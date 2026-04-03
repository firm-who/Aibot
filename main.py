"""
main.py — Application entry point.
"""

from __future__ import annotations
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import BaseModel
from typing import Optional

import config

from webhook import router as webhook_router
from auth import router as auth_router
from background import run_proactive_worker

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.load_remote_config()

    # Auto-register webhooks for all active tenants at startup
    import db
    from telegram_sender import register_webhook
    tenants = db.get_all_tenants()
    for tenant in tenants:
        if tenant.get("telegram_bot_token") and config.APP_URL:
            webhook_url = f"{config.APP_URL}/webhook/{tenant['telegram_bot_token']}"
            try:
                ok = await register_webhook(tenant["telegram_bot_token"], webhook_url)
                print(f"[main] Webhook {'ok' if ok else 'FAILED'}: {tenant['name']}")
            except Exception as e:
                print(f"[main] Webhook error for {tenant['name']}: {e}")

    scheduler.add_job(
        run_proactive_worker,
        trigger="interval",
        seconds=config.WORKER_INTERVAL_SECONDS,
        id="proactive_worker",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    print(f"[main] Background worker started — runs every {config.WORKER_INTERVAL_SECONDS}s")
    yield
    scheduler.shutdown(wait=False)
    print("[main] Scheduler stopped")


app = FastAPI(title="Personal AI Agent", version="1.0.0", lifespan=lifespan)

# ── CORS (allows setting.html opened from file:// or any origin to call the API) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(webhook_router)
app.include_router(auth_router)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/")
async def health():
    return {"status": "ok", "message": "Agent is running"}


@app.get("/health")
async def health_check():
    return {
        "status":    "ok",
        "scheduler": scheduler.running,
        "jobs":      [j.id for j in scheduler.get_jobs()],
    }


# ── Tenant Setup (called by setting.html) ─────────────────────────────────────

class TenantSetupRequest(BaseModel):
    name:               str
    slug:               str
    telegram_bot_token: str


@app.post("/api/setup-tenant")
async def setup_tenant(req: TenantSetupRequest):
    """
    Called by setting.html to:
      1. Validate the Telegram bot token
      2. Create a new tenant row in Supabase
      3. Register the Telegram webhook
    """
    import httpx
    import re
    import db

    # ── Basic validation ──────────────────────────────────────────────
    if len(req.name.strip()) < 2:
        return {"ok": False, "error": "Name must be at least 2 characters"}

    slug = req.slug.strip().lower()
    if not re.match(r'^[a-z0-9][a-z0-9-]{1,}$', slug):
        return {"ok": False, "error": "Slug must be lowercase letters, numbers and hyphens"}

    # ── Validate token with Telegram ──────────────────────────────────
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            me_res = await client.get(
                f"https://api.telegram.org/bot{req.telegram_bot_token}/getMe"
            )
            me = me_res.json()
        except Exception as e:
            return {"ok": False, "error": f"Could not reach Telegram: {e}"}

    if not me.get("ok"):
        return {"ok": False, "error": "Invalid bot token — verify with @BotFather"}

    # ── Check token not already registered ────────────────────────────
    existing = db.get_tenant_by_bot_token(req.telegram_bot_token)
    if existing:
        return {
            "ok": False,
            "error": f"Token already registered to tenant '{existing['name']}'"
        }

    # ── Create tenant ─────────────────────────────────────────────────
    try:
        tenant = db.create_tenant(
            name=req.name.strip(),
            slug=slug,
            bot_token=req.telegram_bot_token,
        )
    except Exception as e:
        return {"ok": False, "error": f"Database error: {e}"}

    if not tenant.get("id"):
        return {"ok": False, "error": "Failed to create tenant — check Supabase logs"}

    # ── Register Telegram webhook ─────────────────────────────────────
    webhook_registered = False
    if config.APP_URL:
        try:
            from telegram_sender import register_webhook
            webhook_url = f"{config.APP_URL}/webhook/{req.telegram_bot_token}"
            webhook_registered = await register_webhook(req.telegram_bot_token, webhook_url)
            print(f"[setup-tenant] Webhook {'ok' if webhook_registered else 'FAILED'}: {req.name}")
        except Exception as e:
            print(f"[setup-tenant] Webhook error: {e}")
    else:
        print("[setup-tenant] APP_URL not set — skipping webhook registration")

    return {
        "ok":                True,
        "tenant_id":         tenant["id"],
        "bot_username":      me["result"]["username"],
        "webhook_registered": webhook_registered,
    }


# ── Legacy / utility endpoints ────────────────────────────────────────────────

@app.post("/setup-webhook")
async def setup_webhook(bot_token: str):
    from telegram_sender import register_webhook
    webhook_url = f"{config.APP_URL}/webhook/{bot_token}"
    ok = await register_webhook(bot_token, webhook_url)
    return {"ok": ok, "webhook_url": webhook_url}


@app.get("/setup-db-sql")
async def get_setup_sql():
    sql = """
-- Run this in your Supabase SQL editor once

create extension if not exists vector;

create table if not exists users (
  id                  uuid primary key default gen_random_uuid(),
  telegram_chat_id    bigint unique not null,
  tenant_id           uuid references tenants(id) on delete cascade,
  name                text,
  llm_api_key         text,
  llm_model           text default 'gpt-4o-mini',
  llm_base_url        text,
  embedding_api_key   text,
  embedding_model     text default 'text-embedding-3-small',
  embedding_base_url  text,
  telegram_bot_token  text,
  gmail_token         jsonb,
  google_client_id    text,
  google_secret       text,
  timezone            text default 'UTC',
  last_active_at      timestamptz default now(),
  created_at          timestamptz default now()
);

create table if not exists memories (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid references users(id) on delete cascade,
  tenant_id        uuid references tenants(id) on delete cascade,
  content          text not null,
  embedding        vector(1536),
  memory_type      text default 'fact',
  importance       int  default 5,
  created_at       timestamptz default now(),
  last_recalled_at timestamptz
);
create index if not exists memories_embedding_idx
  on memories using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create table if not exists messages (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references users(id) on delete cascade,
  tenant_id   uuid references tenants(id) on delete cascade,
  role        text not null check (role in ('user', 'assistant', 'tool')),
  content     text not null,
  embedding   vector(1536),
  created_at  timestamptz default now()
);

create table if not exists tasks (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid references users(id) on delete cascade,
  tenant_id           uuid references tenants(id) on delete cascade,
  description         text not null,
  task_type           text default 'general',
  status              text default 'pending',
  follow_up_needed    bool default false,
  follow_up_at        timestamptz,
  result              text,
  completed_at        timestamptz,
  created_at          timestamptz default now()
);

create table if not exists behaviour_state (
  user_id                  uuid primary key references users(id) on delete cascade,
  last_proactive_at        timestamptz,
  silence_hours            int default 0,
  preferred_active_hours   text default '9-21',
  daily_briefing_time      text default '09:00',
  last_briefing_at         timestamptz
);

create table if not exists scheduled_jobs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid references users(id) on delete cascade,
  tenant_id   uuid references tenants(id) on delete cascade,
  job_type    text default 'message',
  cron_expr   text,
  next_run_at timestamptz,
  payload     jsonb,
  is_active   bool default true,
  created_at  timestamptz default now()
);

create or replace function search_memories(
  p_user_id   uuid,
  p_embedding vector(1536),
  p_limit     int default 6
)
returns table (content text, similarity float)
language sql stable
as $$
  select content,
         1 - (embedding <=> p_embedding) as similarity
  from   memories
  where  user_id = p_user_id
    and  embedding is not null
  order  by embedding <=> p_embedding
  limit  p_limit;
$$;
"""
    return {"sql": sql}


# ── User upsert (kept for backwards compat) ───────────────────────────────────

class UserUpsertRequest(BaseModel):
    telegram_chat_id:   int
    telegram_bot_token: Optional[str] = None
    llm_api_key:        Optional[str] = None
    llm_model:          Optional[str] = None
    llm_base_url:       Optional[str] = None
    embedding_api_key:  Optional[str] = None
    embedding_model:    Optional[str] = None


@app.post("/user/upsert")
async def user_upsert(req: UserUpsertRequest):
    """Create or update a user row in Supabase."""
    from db import upsert_user
    fields = {k: v for k, v in req.dict().items()
              if k != "telegram_chat_id" and v is not None}
    user = upsert_user(req.telegram_chat_id, **fields)
    return {"ok": True, "id": user.get("id")}