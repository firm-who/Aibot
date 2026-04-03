"""
memory.py — lightweight memory: only store what user explicitly asks to remember,
plus basic profile facts extracted once.
"""

from __future__ import annotations
import json
import openai
import db
from config import MEMORY_RECALL_LIMIT

# Keywords that signal the user wants something remembered
_REMEMBER_TRIGGERS = [
    "remember", "don't forget", "keep in mind", "note that", "save that",
    "store that", "make a note", "memorize", "keep this"
]

# Basic profile fields to extract once
_PROFILE_KEYWORDS = [
    "my name is", "i am", "i'm", "i work at", "my company", "my business",
    "i live in", "i'm based in", "my email", "my phone", "i own", "i run"
]


def get_embedding_client(user_config: dict) -> openai.OpenAI:
    kwargs = {"api_key": user_config["embedding_api_key"]}
    if user_config.get("embedding_base_url"):
        kwargs["base_url"] = user_config["embedding_base_url"]
    return openai.OpenAI(**kwargs)


def embed_text(text: str, user_config: dict) -> list[float]:
    client = get_embedding_client(user_config)
    text = text.replace("\n", " ").strip()
    if not text:
        return [0.0] * 1536
    response = client.embeddings.create(
        model=user_config["embedding_model"],
        input=text,
    )
    return response.data[0].embedding


def save_memory_from_text(user_id: str, text: str, user_config: dict,
                          memory_type: str = "fact", importance: int = 5) -> None:
    embedding = embed_text(text, user_config)
    db.save_memory(user_id, text, embedding, memory_type, importance)


def recall_relevant_memories(user_id: str, current_message: str,
                              user_config: dict,
                              limit: int = MEMORY_RECALL_LIMIT) -> list[str]:
    query_embedding = embed_text(current_message, user_config)
    return db.search_memories(user_id, query_embedding, limit)


def build_memory_context(user_id: str, current_message: str,
                         user_config: dict) -> str:
    memories = recall_relevant_memories(user_id, current_message, user_config)
    if not memories:
        return ""
    lines = ["Things I remember about this user:"]
    for i, m in enumerate(memories, 1):
        lines.append(f"  {i}. {m}")
    return "\n".join(lines)


def _has_remember_trigger(text: str) -> bool:
    t = text.lower()
    return any(trigger in t for trigger in _REMEMBER_TRIGGERS)


def _has_profile_info(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _PROFILE_KEYWORDS)


def extract_and_save_facts(user_id: str, user_message: str,
                           assistant_reply: str, user_config: dict) -> None:
    """
    Only saves memory in two cases:
    1. User explicitly asks to remember something
    2. User shares basic profile info (name, company, location)
    """
    msg_lower = user_message.lower()

    # Case 1: explicit remember request
    if _has_remember_trigger(msg_lower):
        _extract_explicit_memory(user_id, user_message, user_config)
        return

    # Case 2: basic profile info
    if _has_profile_info(msg_lower):
        _extract_profile_fact(user_id, user_message, user_config)
        return

    # Otherwise: don't store anything


def _extract_explicit_memory(user_id: str, user_message: str,
                              user_config: dict) -> None:
    """Extract what the user wants remembered and save it."""
    from llm import call_llm_raw
    prompt = f"""The user wants you to remember something. Extract exactly what they want remembered as a single clear sentence.

User said: {user_message}

Respond with ONLY the fact to remember, nothing else. No JSON, no explanation."""

    try:
        raw = call_llm_raw(
            messages=[{"role": "user", "content": prompt}],
            user_config=user_config,
            max_tokens=100,
        ).strip()
        if raw and len(raw) > 5:
            save_memory_from_text(user_id, raw, user_config,
                                  memory_type="user_request", importance=8)
    except Exception as e:
        print(f"[memory] explicit memory error: {e}")


def _extract_profile_fact(user_id: str, user_message: str,
                           user_config: dict) -> None:
    """Extract basic profile info (name, company, location) from message."""
    from llm import call_llm_raw
    prompt = f"""Extract basic profile information from this message (name, company, job, location only).

User said: {user_message}

Respond with ONLY a single clear fact sentence like "User's name is Richard" or "User works at Andes Media".
If nothing worth saving, respond: SKIP"""

    try:
        raw = call_llm_raw(
            messages=[{"role": "user", "content": prompt}],
            user_config=user_config,
            max_tokens=60,
        ).strip()
        if raw and raw != "SKIP" and len(raw) > 5:
            save_memory_from_text(user_id, raw, user_config,
                                  memory_type="profile", importance=9)
    except Exception as e:
        print(f"[memory] profile fact error: {e}")


def rebuild_user_profile(user_id: str, user_config: dict) -> None:
    """Summarise all memories into a concise profile. Called daily by background worker."""
    from llm import call_llm_raw
    memories = db.get_all_memories(user_id)
    if not memories:
        return
    mem_text = "\n".join([m["content"] for m in memories])
    summary = call_llm_raw(
        messages=[{"role": "user", "content": f"""Summarise everything known about this user into a concise profile (max 150 words). Include: name, business, industry, goals, clients, preferences, important context.

Known facts:
{mem_text}

Write ONLY the profile summary, present tense, third person."""}],
        user_config=user_config,
        max_tokens=200,
    ).strip()
    if summary:
        db.get_client().table("users").update(
            {"profile_summary": summary}
        ).eq("id", user_id).execute()
        print(f"[memory] profile rebuilt for {user_id}")
