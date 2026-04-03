"""
llm.py — LLM call functions.

Supports OpenAI-compatible APIs and Anthropic natively.
Which provider to use is determined by the user's config (from Supabase first,
then platform fallback). The model name is the signal:
  - starts with "claude-"  → use Anthropic SDK
  - anything else          → use OpenAI-compatible SDK (works for GPT, Gemini,
                             Together, Mistral, local Ollama, etc.)
"""

from __future__ import annotations
import openai
import anthropic as _anthropic
from config import RECENT_MESSAGES_LIMIT


# ═══════════════════════════════════════════════════════════════════════════════
#  CLIENT BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════

def _openai_client(user_config: dict) -> openai.OpenAI:
    kwargs = {"api_key": user_config["llm_api_key"]}
    if user_config.get("llm_base_url"):
        kwargs["base_url"] = user_config["llm_base_url"]
    return openai.OpenAI(**kwargs)


def _anthropic_client(user_config: dict) -> _anthropic.Anthropic:
    return _anthropic.Anthropic(api_key=user_config["llm_api_key"])


def _is_anthropic_model(model: str) -> bool:
    return model.lower().startswith("claude")


# ═══════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_PERSONA = """You are a highly capable personal AI assistant and best friend 
helping a business owner or professional with their daily work and life.

Your personality:
- Warm, personal, and proactive — you genuinely care about the user
- Professional but friendly — like a trusted colleague who knows you well
- Honest and direct — you tell them what they need to hear
- Proactive — you notice things and bring them up without being asked
- Extremely concise — short replies only, max 3 sentences unless detail is truly needed. Never repeat yourself. Never ask multiple questions.

Your capabilities:
- Send and read emails via Gmail
- Search the web using DuckDuckGo
- Automate browser tasks (filling forms, ordering, logging into sites)
- Remember everything the user tells you across all conversations
- Manage tasks and follow up on them proactively

Important rules:
- Always use the user's memories and past context to personalise your replies
- If you completed a task (email sent, search done), confirm it clearly
- If something needs follow-up later, say so and remember it
- Never say you "can't remember" — check the memory context provided
- Never make up facts, numbers, or information — if unsure, search first
- If you don't know something for certain, say so and offer to search
- Speak naturally, not like a bot

Tool usage rules:
- NEVER state facts, prices, news, people, companies, or current events from memory — always use web_search first
- Use web_search for ALL information lookup, research, and reading tasks
- Use browse_url ONLY when the task requires filling forms, clicking buttons, or logging in
- Never use browse_url just to visit a webpage and read it — use web_search for that instead, as it's faster and cheaper
"""

def build_system_prompt(memory_context: str = "",
                        user_name: str = "",
                        user: dict = {}) -> str:
    prompt = AGENT_PERSONA
    if user_name:
        prompt += f"\n\nThe user's name is {user_name}."
    if user.get("profile_summary"):
        prompt += f"\n\nWhat I know about this user:\n{user['profile_summary']}"
    if memory_context:
        prompt += f"\n\n{memory_context}"
    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS  (what the LLM can call)
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
#  TOOL DEFINITIONS  — replace the existing TOOLS = [...] block in llm.py
# ═══════════════════════════════════════════════════════════════════════════════

TOOLS = [
    # ── Gmail ──────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send an email via Gmail",
            "parameters": {
                "type": "object",
                "properties": {
                    "to":      {"type": "string"},
                    "subject": {"type": "string"},
                    "body":    {"type": "string"},
                },
                "required": ["to", "subject", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_emails",
            "description": "Read recent unread emails from Gmail inbox",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": [],
            },
        },
    },

    # ── Web search ─────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information, news, facts, prices, reviews, people, companies, addresses, phone numbers, opening hours, or any question that can be answered by reading a webpage. Use this FIRST for any research or lookup task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string"},
                    "max_results": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
        },
    },

    # ── Browser ────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "browse_url",
            "description": "Open a URL in a cloud browser, take screenshots, fill forms. Use ONLY when you need to interact with a webpage — fill a form, click a button, log into a site, submit an order, book an appointment, or perform any action that requires controlling a browser. Do NOT use this just to read or look up information — use web_search instead. This tool costs money to run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":         {"type": "string"},
                    "instruction": {"type": "string"},
                    "screenshot_before_submit": {"type": "boolean", "default": True},
                },
                "required": ["url", "instruction"],
            },
        },
    },

    # ── Tasks ──────────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "save_task_with_followup",
            "description": "Save a task that needs a follow-up check later",
            "parameters": {
                "type": "object",
                "properties": {
                    "description":        {"type": "string"},
                    "follow_up_in_hours": {"type": "number"},
                },
                "required": ["description", "follow_up_in_hours"],
            },
        },
    },

    # ── Google Drive ───────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "drive_search",
            "description": "Search for files in Google Drive by name or keyword",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "File name or keyword to search"},
                    "max_results": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_read",
            "description": "Read the text content of a Google Drive file (Doc, Sheet, txt)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["file_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_create_doc",
            "description": "Create a new Google Doc with optional title, content and heading",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":     {"type": "string"},
                    "content":   {"type": "string", "description": "Body text to insert"},
                    "heading":   {"type": "string", "description": "Optional H1 heading"},
                    "folder_id": {"type": "string", "description": "Optional folder ID to put it in"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_create_sheet",
            "description": "Create a new blank Google Sheet",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":     {"type": "string"},
                    "folder_id": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_share",
            "description": "Share a Drive file with someone or make it public",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string"},
                    "email":   {"type": "string", "description": "Email to share with"},
                    "role":    {"type": "string", "enum": ["reader", "commenter", "writer"], "default": "reader"},
                    "anyone":  {"type": "boolean", "description": "If true, make public", "default": False},
                },
                "required": ["file_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_list_folder",
            "description": "List files in a Drive folder",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id":   {"type": "string", "default": "root"},
                    "max_results": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        },
    },

    # ── Google Sheets ──────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "sheets_read",
            "description": "Read data from a Google Sheet. Returns headers and rows.",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                    "range":          {"type": "string", "description": "e.g. 'Sheet1' or 'Sheet1!A1:D20'", "default": "Sheet1"},
                },
                "required": ["spreadsheet_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_write",
            "description": "Write a 2D array of values to a specific range in a sheet",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                    "range":          {"type": "string", "description": "e.g. 'Sheet1!A1'"},
                    "values":         {"type": "array", "items": {"type": "array"}, "description": "2D array of values"},
                },
                "required": ["spreadsheet_id", "range", "values"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_append",
            "description": "Append rows to the bottom of a sheet",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                    "sheet_name":     {"type": "string", "default": "Sheet1"},
                    "rows":           {"type": "array", "items": {"type": "array"}, "description": "Rows to append"},
                },
                "required": ["spreadsheet_id", "rows"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_find_update",
            "description": "Find rows where a column matches a value and update other columns in those rows",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                    "sheet_name":     {"type": "string", "default": "Sheet1"},
                    "search_column":  {"type": "string", "description": "Column header to search in"},
                    "search_value":   {"type": "string", "description": "Value to find"},
                    "updates":        {"type": "object", "description": "Dict of {column_name: new_value}"},
                },
                "required": ["spreadsheet_id", "search_column", "search_value", "updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_format",
            "description": "Apply formatting to a range in a sheet (bold, colors, font size, alignment)",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id":     {"type": "string"},
                    "sheet_name":         {"type": "string"},
                    "range":              {"type": "string"},
                    "bold":               {"type": "boolean", "default": False},
                    "font_size":          {"type": "integer"},
                    "horizontal_alignment": {"type": "string", "enum": ["LEFT", "CENTER", "RIGHT"]},
                    "background_color":   {"type": "object", "description": "{red, green, blue} values 0-1"},
                    "text_color":         {"type": "object", "description": "{red, green, blue} values 0-1"},
                },
                "required": ["spreadsheet_id", "sheet_name", "range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_chart",
            "description": "Create a chart in a Google Sheet",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                    "sheet_name":     {"type": "string"},
                    "chart_type":     {"type": "string", "enum": ["BAR", "LINE", "PIE", "COLUMN", "SCATTER", "AREA"]},
                    "data_range":     {"type": "string", "description": "e.g. 'Sheet1!A1:B10'"},
                    "title":          {"type": "string", "default": ""},
                    "position_row":   {"type": "integer", "default": 1},
                    "position_col":   {"type": "integer", "default": 6},
                },
                "required": ["spreadsheet_id", "sheet_name", "chart_type", "data_range"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sheets_info",
            "description": "Get metadata about a spreadsheet: title, sheet names, row/column counts",
            "parameters": {
                "type": "object",
                "properties": {
                    "spreadsheet_id": {"type": "string"},
                },
                "required": ["spreadsheet_id"],
            },
        },
    },

    # ── Google Docs ────────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "docs_read",
            "description": "Read the full content of a Google Doc",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                },
                "required": ["document_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_create",
            "description": "Create a new Google Doc with title, optional heading and body content",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":   {"type": "string"},
                    "heading": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_append",
            "description": "Append text to the end of an existing Google Doc",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id":   {"type": "string"},
                    "text":          {"type": "string"},
                    "as_heading":    {"type": "boolean", "default": False},
                    "heading_level": {"type": "integer", "enum": [1, 2, 3, 4, 5, 6], "default": 2},
                },
                "required": ["document_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docs_replace",
            "description": "Find and replace text in a Google Doc",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_id":  {"type": "string"},
                    "find":         {"type": "string"},
                    "replace_with": {"type": "string"},
                },
                "required": ["document_id", "find", "replace_with"],
            },
        },
    },

    # ── Google Business Profile ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "gbp_accounts",
            "description": "List all Google Business Profile accounts the user has access to",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gbp_locations",
            "description": "List all business locations under a Business Profile account",
            "parameters": {
                "type": "object",
                "properties": {
                    "account_name": {"type": "string", "description": "e.g. 'accounts/123456789'"},
                },
                "required": ["account_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gbp_reviews",
            "description": "Get Google reviews for a business location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location_name": {"type": "string", "description": "e.g. 'accounts/123/locations/456'"},
                    "max_results":   {"type": "integer", "default": 10},
                },
                "required": ["location_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gbp_reply_review",
            "description": "Reply to a Google Business review",
            "parameters": {
                "type": "object",
                "properties": {
                    "location_name": {"type": "string"},
                    "review_id":     {"type": "string"},
                    "reply_text":    {"type": "string"},
                },
                "required": ["location_name", "review_id", "reply_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gbp_post",
            "description": "Create a post/update on Google Business Profile",
            "parameters": {
                "type": "object",
                "properties": {
                    "location_name": {"type": "string"},
                    "summary":       {"type": "string", "description": "Post text"},
                    "post_type":     {"type": "string", "enum": ["STANDARD", "EVENT", "OFFER", "PRODUCT"], "default": "STANDARD"},
                    "action_type":   {"type": "string", "enum": ["BOOK", "ORDER", "SHOP", "LEARN_MORE", "SIGN_UP", "CALL"]},
                    "action_url":    {"type": "string"},
                    "event_title":   {"type": "string"},
                    "offer_coupon":  {"type": "string"},
                },
                "required": ["location_name", "summary"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gbp_upload_photo",
            "description": "Upload a photo to Google Business Profile (profile, cover, interior, food, etc.)",
            "parameters": {
                "type": "object",
                "properties": {
                    "location_name":  {"type": "string"},
                    "image_base64":   {"type": "string", "description": "Base64-encoded JPEG or PNG image"},
                    "category":       {
                        "type": "string",
                        "enum": ["PROFILE", "COVER", "EXTERIOR", "INTERIOR", "PRODUCT",
                                 "AT_WORK", "FOOD_AND_DRINK", "MENU", "COMMON_AREA",
                                 "ROOMS", "TEAMS", "ADDITIONAL"],
                        "default": "ADDITIONAL",
                    },
                    "description":    {"type": "string", "default": ""},
                },
                "required": ["location_name", "image_base64"],
            },
        },
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN CALL FUNCTION  (with tool use)
# ═══════════════════════════════════════════════════════════════════════════════

def call_llm(user: dict, user_config: dict,
             conversation_history: list[dict],
             memory_context: str = "") -> dict:
    """
    Main LLM call with tool use support.

    Returns a dict:
        {
            "text": "...",           # Final text reply (may be empty if only tool calls)
            "tool_calls": [...],     # List of tool call dicts if any
            "finish_reason": "...",  # "stop" or "tool_calls"
        }
    """
    model  = user_config["llm_model"]
    system = build_system_prompt(
        memory_context=memory_context,
        user_name=user.get("name", ""),
        user=user,
    )

    if _is_anthropic_model(model):
        return _call_anthropic(user_config, system, conversation_history)
    else:
        return _call_openai_compatible(user_config, system, conversation_history)


def call_llm_raw(messages: list[dict], user_config: dict,
                 max_tokens: int = 500) -> str:
    """
    Simple LLM call with no tools, no system prompt magic.
    Used for internal tasks like fact extraction.
    Returns the text content string directly.
    """
    model = user_config["llm_model"]
    if _is_anthropic_model(model):
        client = _anthropic_client(user_config)
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.content[0].text if resp.content else ""
    else:
        client = _openai_client(user_config)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=messages,
        )
        return resp.choices[0].message.content or ""


# ═══════════════════════════════════════════════════════════════════════════════
#  PROVIDER-SPECIFIC IMPLEMENTATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _call_openai_compatible(user_config: dict, system: str,
                            history: list[dict]) -> dict:
    client = _openai_client(user_config)
    messages = [{"role": "system", "content": system}] + history

    resp = client.chat.completions.create(
        model=user_config["llm_model"],
        messages=messages,
        tools=TOOLS,
        tool_choice="auto",
        max_tokens=1000,
    )

    choice = resp.choices[0]
    msg    = choice.message

    tool_calls = []
    if msg.tool_calls:
        import json
        for tc in msg.tool_calls:
            tool_calls.append({
                "id":        tc.id,
                "name":      tc.function.name,
                "arguments": json.loads(tc.function.arguments),
            })

    return {
        "text":          msg.content or "",
        "tool_calls":    tool_calls,
        "finish_reason": choice.finish_reason,
    }


def _call_anthropic(user_config: dict, system: str,
                    history: list[dict]) -> dict:
    """
    Anthropic uses a different tool format. We convert our OpenAI-style
    TOOLS definition to Anthropic format on the fly.
    """
    client = _anthropic_client(user_config)

    # Convert tools to Anthropic format
    anthropic_tools = []
    for t in TOOLS:
        fn = t["function"]
        anthropic_tools.append({
            "name":         fn["name"],
            "description":  fn["description"],
            "input_schema": fn["parameters"],
        })

    resp = client.messages.create(
        model=user_config["llm_model"],
        system=system,
        messages=history,
        tools=anthropic_tools,
        max_tokens=1000,
    )

    text       = ""
    tool_calls = []

    for block in resp.content:
        if block.type == "text":
            text += block.text
        elif block.type == "tool_use":
            tool_calls.append({
                "id":        block.id,
                "name":      block.name,
                "arguments": block.input,
            })

    finish = "tool_calls" if tool_calls else "stop"
    return {"text": text, "tool_calls": tool_calls, "finish_reason": finish}


# ═══════════════════════════════════════════════════════════════════════════════
#  PROACTIVE MESSAGE GENERATION  (used by background worker)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_proactive_message(reason: str, context: str,
                               user_config: dict,
                               user_name: str = "") -> str:
    """
    Generate a natural, friendly proactive message from the agent to the user.
    Called by background.py when the agent decides to reach out.

    reason  — why the agent is reaching out, e.g. "follow-up on sent email"
    context — relevant facts/memories to make the message personal
    """
    prompt = f"""You are a proactive AI assistant reaching out to your user.

Reason you are messaging them: {reason}

Context and relevant memories:
{context}

Write a short, friendly, personal Telegram message to the user.
- Be warm and natural, like a helpful best friend, not a robot
- Reference specific details from the context to show you remember
- Keep it under 3 sentences
- End with a clear question or action they can take
- Do NOT start with "Hello" or "Hi [name]" every time — vary your opening
{"- User's name: " + user_name if user_name else ""}

Write ONLY the message text, nothing else."""

    return call_llm_raw(
        messages=[{"role": "user", "content": prompt}],
        user_config=user_config,
        max_tokens=200,
    )


def generate_daily_briefing(memories: list[dict], pending_tasks: list[dict],
                             user_config: dict, user_name: str = "") -> str:
    """Generate a morning briefing message summarising what's going on."""
    memory_text = "\n".join([m["content"] for m in memories[:10]])
    task_text   = "\n".join([t["description"] for t in pending_tasks[:5]])

    prompt = f"""Generate a short, warm morning briefing message for a business owner.

What you know about them and their work:
{memory_text or "Not much yet — this may be an early user."}

Pending tasks / follow-ups:
{task_text or "No pending tasks."}

Write a natural, friendly good morning message (3-5 sentences max).
Highlight the most important things for their day.
Be like a smart assistant who knows their work well, not a generic bot.
{"User's name: " + user_name if user_name else ""}

Write ONLY the message, nothing else."""

    return call_llm_raw(
        messages=[{"role": "user", "content": prompt}],
        user_config=user_config,
        max_tokens=300,
    )
