"""
memory.py — embedding generation + semantic memory operations.

All embedding calls go through here. The embedding provider (model + key + base_url)
is resolved from the user's Supabase config first, then falls back to platform defaults.
This means different users can use different embedding APIs if they want.
"""

from __future__ import annotations
import json
import openai
import db
from config import MEMORY_RECALL_LIMIT


# ═══════════════════════════════════════════════════════════════════════════════
#  EMBEDDING
# ═══════════════════════════════════════════════════════════════════════════════

def get_embedding_client(user_config: dict) -> openai.OpenAI:
    """
    Build an OpenAI-compatible client using the user's embedding config.
    Works with any OpenAI-compatible API (OpenAI, Together, local Ollama, etc.)
    """
    kwargs = {"api_key": user_config["embedding_api_key"]}
    if user_config.get("embedding_base_url"):
        kwargs["base_url"] = user_config["embedding_base_url"]
    return openai.OpenAI(**kwargs)


def embed_text(text: str, user_config: dict) -> list[float]:
    """
    Convert any text string into a 1536-dimension vector using the configured
    embedding model. Returns a list of floats.

    Cost note: text-embedding-3-small costs ~$0.02 per 1M tokens.
    An average message is ~50 tokens = $0.000001 per message. Basically free.
    """
    client = get_embedding_client(user_config)
    text = text.replace("\n", " ").strip()
    if not text:
        # Return zero vector for empty text rather than crashing
        return [0.0] * 1536

    response = client.embeddings.create(
        model=user_config["embedding_model"],
        input=text,
    )
    return response.data[0].embedding


# ═══════════════════════════════════════════════════════════════════════════════
#  SAVING MEMORIES
# ═══════════════════════════════════════════════════════════════════════════════

def save_memory_from_text(user_id: str, text: str, user_config: dict,
                          memory_type: str = "fact", importance: int = 5) -> None:
    """Embed text and store as a memory in Supabase."""
    embedding = embed_text(text, user_config)
    db.save_memory(user_id, text, embedding, memory_type, importance)


# ═══════════════════════════════════════════════════════════════════════════════
#  SEARCHING MEMORIES
# ═══════════════════════════════════════════════════════════════════════════════

def recall_relevant_memories(user_id: str, current_message: str,
                              user_config: dict,
                              limit: int = MEMORY_RECALL_LIMIT) -> list[str]:
    """
    Given the current message, find the most semantically similar memories.
    Returns a list of memory strings sorted by relevance.

    Example: if user says "meeting with Tanaka tomorrow", this will return
    memories like "Tanaka-san is from Osaka", "Tanaka deal worth 500k yen",
    even though those exact words weren't in the query.
    """
    query_embedding = embed_text(current_message, user_config)
    return db.search_memories(user_id, query_embedding, limit)


# ═══════════════════════════════════════════════════════════════════════════════
#  EXTRACTING AND SAVING FACTS FROM CONVERSATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def extract_and_save_facts(user_id: str, user_message: str,
                           assistant_reply: str, user_config: dict) -> None:
    """
    Ask the LLM to extract key facts from this exchange and save each one
    as a separate memory. This is called after every conversation turn.

    Facts extracted include: names, companies, tasks done, preferences mentioned,
    decisions made, deadlines set, relationships described, anything the user
    shared about themselves or their business.
    """
    from llm import call_llm_raw

    extraction_prompt = f"""You are a memory extraction system. 
Read this conversation exchange and extract important facts worth remembering.

User said: {user_message}
Assistant replied: {assistant_reply}

Extract facts as a JSON array. Each fact should be a short, clear sentence.
Only extract genuinely useful long-term facts (names, companies, preferences,
decisions, tasks completed, deadlines, relationships).
Skip small talk and obvious things.

Respond ONLY with a JSON array like:
["fact 1", "fact 2", "fact 3"]

If no important facts, respond: []"""

    try:
        raw = call_llm_raw(
            messages=[{"role": "user", "content": extraction_prompt}],
            user_config=user_config,
            max_tokens=400,
        )
        # Strip any markdown fences
        raw = raw.strip().strip("```json").strip("```").strip()
        facts: list[str] = json.loads(raw)

        for fact in facts:
            if fact and len(fact) > 10:
                # Assign importance based on content keywords
                importance = _score_importance(fact)
                save_memory_from_text(user_id, fact, user_config,
                                      memory_type="extracted_fact",
                                      importance=importance)
    except Exception as e:
        # Never crash the main flow due to memory extraction failure
        print(f"[memory] extract_and_save_facts error: {e}")


def _score_importance(fact: str) -> int:
    """
    Simple heuristic to score how important a fact is (1-10).
    Higher = more likely to be recalled in future searches.
    """
    high_keywords = ["client", "deal", "payment", "deadline", "password",
                     "address", "phone", "important", "urgent", "contract",
                     "invoice", "meeting", "boss", "partner", "supplier"]
    low_keywords  = ["likes", "prefers", "usually", "sometimes", "often"]

    fact_lower = fact.lower()
    if any(k in fact_lower for k in high_keywords):
        return 8
    if any(k in fact_lower for k in low_keywords):
        return 4
    return 5


# ═══════════════════════════════════════════════════════════════════════════════
#  BUILDING MEMORY CONTEXT STRING  (for injection into LLM prompts)
# ═══════════════════════════════════════════════════════════════════════════════

def build_memory_context(user_id: str, current_message: str,
                         user_config: dict) -> str:
    """
    Returns a formatted string of relevant memories ready to inject into
    the system prompt. Returns empty string if no memories found.
    """
    memories = recall_relevant_memories(user_id, current_message, user_config)
    if not memories:
        return ""

    lines = ["Relevant things I remember about this user:"]
    for i, m in enumerate(memories, 1):
        lines.append(f"  {i}. {m}")
    return "\n".join(lines)
