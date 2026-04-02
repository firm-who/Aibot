"""
tools/executor.py — Executes all tool calls that the LLM requests.

Tools available:
  Gmail:            send_email, read_emails
  Web:              web_search
  Browser:          browse_url
  Tasks:            save_task_with_followup
  Drive:            drive_search, drive_read, drive_create_doc, drive_create_sheet,
                    drive_upload, drive_share, drive_list_folder
  Sheets:           sheets_read, sheets_write, sheets_append, sheets_find_update,
                    sheets_format, sheets_chart, sheets_info
  Docs:             docs_read, docs_create, docs_append, docs_replace, docs_table
  Business Profile: gbp_accounts, gbp_locations, gbp_reviews, gbp_reply_review,
                    gbp_post, gbp_upload_photo
"""

from __future__ import annotations
import json
import db
from datetime import datetime, timezone, timedelta


async def execute_tool(tool_name: str, arguments: dict, user: dict,
                       user_config: dict, telegram_chat_id: int,
                       bot_token: str = "") -> str:
    try:
        gmail_token = user_config.get("gmail_token")

        # ── Gmail ──────────────────────────────────────────────────────────────
        if tool_name == "send_email":
            return await _execute_send_email(arguments, user_config, user["id"])

        elif tool_name == "read_emails":
            return await _execute_read_emails(arguments, user_config)

        # ── Web ────────────────────────────────────────────────────────────────
        elif tool_name == "web_search":
            return await _execute_web_search(arguments)

        # ── Browser ────────────────────────────────────────────────────────────
        elif tool_name == "browse_url":
            return await _execute_browse_url(
                arguments, user_config, user, telegram_chat_id, bot_token
            )

        # ── Tasks ──────────────────────────────────────────────────────────────
        elif tool_name == "save_task_with_followup":
            return await _execute_save_task(arguments, user["id"])

        # ── Google Drive ───────────────────────────────────────────────────────
        elif tool_name == "drive_search":
            return await _drive_search(arguments, gmail_token)

        elif tool_name == "drive_read":
            return await _drive_read(arguments, gmail_token)

        elif tool_name == "drive_create_doc":
            return await _drive_create_doc(arguments, gmail_token)

        elif tool_name == "drive_create_sheet":
            return await _drive_create_sheet(arguments, gmail_token)

        elif tool_name == "drive_share":
            return await _drive_share(arguments, gmail_token)

        elif tool_name == "drive_list_folder":
            return await _drive_list_folder(arguments, gmail_token)

        # ── Google Sheets ──────────────────────────────────────────────────────
        elif tool_name == "sheets_read":
            return await _sheets_read(arguments, gmail_token)

        elif tool_name == "sheets_write":
            return await _sheets_write(arguments, gmail_token)

        elif tool_name == "sheets_append":
            return await _sheets_append(arguments, gmail_token)

        elif tool_name == "sheets_find_update":
            return await _sheets_find_update(arguments, gmail_token)

        elif tool_name == "sheets_format":
            return await _sheets_format(arguments, gmail_token)

        elif tool_name == "sheets_chart":
            return await _sheets_chart(arguments, gmail_token)

        elif tool_name == "sheets_info":
            return await _sheets_info(arguments, gmail_token)

        # ── Google Docs ────────────────────────────────────────────────────────
        elif tool_name == "docs_read":
            return await _docs_read(arguments, gmail_token)

        elif tool_name == "docs_create":
            return await _docs_create(arguments, gmail_token)

        elif tool_name == "docs_append":
            return await _docs_append(arguments, gmail_token)

        elif tool_name == "docs_replace":
            return await _docs_replace(arguments, gmail_token)

        # ── Google Business Profile ────────────────────────────────────────────
        elif tool_name == "gbp_accounts":
            return await _gbp_accounts(gmail_token)

        elif tool_name == "gbp_locations":
            return await _gbp_locations(arguments, gmail_token)

        elif tool_name == "gbp_reviews":
            return await _gbp_reviews(arguments, gmail_token)

        elif tool_name == "gbp_reply_review":
            return await _gbp_reply_review(arguments, gmail_token)

        elif tool_name == "gbp_post":
            return await _gbp_post(arguments, gmail_token)

        elif tool_name == "gbp_upload_photo":
            return await _gbp_upload_photo(arguments, gmail_token)

        else:
            return f"Unknown tool: {tool_name}"

    except Exception as e:
        return f"Tool '{tool_name}' failed: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  GMAIL
# ═══════════════════════════════════════════════════════════════════════════════

async def _execute_send_email(args: dict, user_config: dict, user_id: str) -> str:
    from email import send_email
    gmail_token = user_config.get("gmail_token")
    if not gmail_token:
        return "Cannot send email — Gmail not connected. Ask user to say 'connect my Gmail'."
    result = send_email(
        to=args["to"], subject=args["subject"], body=args["body"],
        gmail_token=gmail_token,
    )
    if result["success"]:
        follow_up_at = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        db.save_task(user_id=user_id,
                     description=f"Sent email to {args['to']}: {args['subject']}",
                     task_type="email_sent", follow_up_at=follow_up_at)
        return f"✓ Email sent to {args['to']}. I'll check for a reply in 24h."
    return f"Failed to send email: {result['error']}"


async def _execute_read_emails(args: dict, user_config: dict) -> str:
    from email import read_emails
    gmail_token = user_config.get("gmail_token")
    if not gmail_token:
        return "Gmail not connected."
    emails = read_emails(gmail_token, max_results=args.get("max_results", 5))
    if not emails:
        return "No unread emails."
    if "error" in emails[0]:
        return f"Error: {emails[0]['error']}"
    lines = [f"Found {len(emails)} unread email(s):\n"]
    for i, e in enumerate(emails, 1):
        lines.append(f"{i}. From: {e['from']}\n   Subject: {e['subject']}\n   {e['snippet']}\n")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  WEB + BROWSER + TASKS  (unchanged)
# ═══════════════════════════════════════════════════════════════════════════════

async def _execute_web_search(args: dict) -> str:
    from search import web_search
    return web_search(query=args["query"], max_results=args.get("max_results", 5))


async def _execute_browse_url(args: dict, user_config: dict,
                               user: dict, telegram_chat_id: int, bot_token: str) -> str:
    from browser import browse_url
    from telegram_sender import send_photo_base64
    result = await browse_url(
        url=args["url"], instruction=args["instruction"],
        user_config=user_config,
        screenshot_before_submit=args.get("screenshot_before_submit", True),
    )
    if result.get("screenshot_base64") and bot_token:
        caption = ("I've filled the form. Reply *yes* to submit or *no* to cancel."
                   if result.get("needs_confirmation") else "Done! Here's a screenshot.")
        await send_photo_base64(bot_token, telegram_chat_id, result["screenshot_base64"], caption)
    if result.get("needs_confirmation") and result.get("session_id"):
        db.save_pending_browser_session(
            user_id=user["id"], session_id=result["session_id"],
            instruction=args["instruction"], url=args["url"],
        )
        return "Screenshot sent. Waiting for your yes/no confirmation."
    return result.get("result", "Browser action completed.")


async def _execute_save_task(args: dict, user_id: str) -> str:
    hours = args.get("follow_up_in_hours", 24)
    follow_up_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
    db.save_task(user_id=user_id, description=args["description"],
                 task_type="manual", follow_up_at=follow_up_at)
    return f"Task saved. I'll follow up in {hours} hours."


# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE DRIVE
# ═══════════════════════════════════════════════════════════════════════════════

def _require_google(gmail_token) -> str | None:
    if not gmail_token:
        return "Google not connected. Ask user to say 'connect my Gmail'."
    return None

async def _drive_search(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import search_files
    files = search_files(gmail_token, args["query"], args.get("max_results", 10))
    if not files: return "No files found."
    lines = [f"Found {len(files)} file(s):"]
    for f in files:
        lines.append(f"• {f['name']} ({f['mimeType'].split('.')[-1]}) — {f.get('webViewLink','')}")
    return "\n".join(lines)

async def _drive_read(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import get_file_content
    result = get_file_content(gmail_token, args["file_id"])
    content = result["content"]
    if len(content) > 3000:
        content = content[:3000] + "\n\n[...truncated — file is larger]"
    return f"**{result['name']}**\n\n{content}"

async def _drive_create_doc(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import create_document
    result = create_document(gmail_token, args["title"],
                             args.get("content", ""), args.get("folder_id"))
    return f"✓ Doc created: {result['title']}\n{result['webViewLink']}"

async def _drive_create_sheet(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import create_spreadsheet
    result = create_spreadsheet(gmail_token, args["title"], args.get("folder_id"))
    return f"✓ Sheet created: {result['name']}\n{result['webViewLink']}"

async def _drive_share(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import share_file
    url = share_file(gmail_token, args["file_id"],
                     args.get("email"), args.get("role", "reader"),
                     args.get("anyone", False))
    return f"✓ File shared. Link: {url}"

async def _drive_list_folder(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.drive import list_folder
    files = list_folder(gmail_token, args.get("folder_id", "root"), args.get("max_results", 20))
    if not files: return "Folder is empty."
    lines = [f"Found {len(files)} item(s):"]
    for f in files:
        lines.append(f"• {f['name']} ({f['mimeType'].split('.')[-1]})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE SHEETS
# ═══════════════════════════════════════════════════════════════════════════════

async def _sheets_read(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import read_range
    data = read_range(gmail_token, args["spreadsheet_id"], args.get("range", "Sheet1"))
    if not data["rows"]: return "No data found in that range."
    lines = [f"Headers: {', '.join(data['headers'])}", f"{len(data['rows'])} rows:"]
    for row in data["rows"][:20]:
        lines.append(str(row))
    if len(data["rows"]) > 20:
        lines.append(f"... and {len(data['rows'])-20} more rows")
    return "\n".join(lines)

async def _sheets_write(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import write_range
    result = write_range(gmail_token, args["spreadsheet_id"],
                         args["range"], args["values"])
    return f"✓ Written {result['updated_cells']} cells to {result['updated_range']}."

async def _sheets_append(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import append_rows
    result = append_rows(gmail_token, args["spreadsheet_id"],
                         args["sheet_name"], args["rows"])
    return f"✓ Appended {result['appended_rows']} row(s) to {result['updated_range']}."

async def _sheets_find_update(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import find_and_update
    result = find_and_update(gmail_token, args["spreadsheet_id"],
                              args["sheet_name"], args["search_column"],
                              args["search_value"], args["updates"])
    if "error" in result: return f"Error: {result['error']}"
    return f"✓ Updated {result['updated_rows']} row(s) where {args['search_column']} = {args['search_value']}."

async def _sheets_format(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import format_range
    return format_range(gmail_token, args["spreadsheet_id"], args["sheet_name"],
                        args["range"], bold=args.get("bold", False),
                        background_color=args.get("background_color"),
                        text_color=args.get("text_color"),
                        font_size=args.get("font_size"),
                        horizontal_alignment=args.get("horizontal_alignment"))

async def _sheets_chart(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import create_chart
    return create_chart(gmail_token, args["spreadsheet_id"], args["sheet_name"],
                        args["chart_type"], args["data_range"],
                        args.get("title", ""), args.get("position_row", 1),
                        args.get("position_col", 6))

async def _sheets_info(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.sheets import get_sheet_info
    info = get_sheet_info(gmail_token, args["spreadsheet_id"])
    lines = [f"📊 {info['title']}", f"URL: {info['url']}", "Sheets:"]
    for s in info["sheets"]:
        lines.append(f"  • {s['name']} ({s['rows']} rows × {s['columns']} cols)")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE DOCS
# ═══════════════════════════════════════════════════════════════════════════════

async def _docs_read(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.docs import read_document
    doc = read_document(gmail_token, args["document_id"])
    content = doc["content"]
    if len(content) > 3000:
        content = content[:3000] + "\n\n[...truncated]"
    return f"**{doc['title']}** ({doc['word_count']} words)\n\n{content}"

async def _docs_create(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.docs import create_document
    doc = create_document(gmail_token, args["title"],
                          args.get("content", ""), args.get("heading"))
    return f"✓ Doc created: {doc['title']}\n{doc['url']}"

async def _docs_append(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.docs import append_to_document
    return append_to_document(gmail_token, args["document_id"], args["text"],
                               args.get("as_heading", False), args.get("heading_level", 2))

async def _docs_replace(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.docs import replace_text
    result = replace_text(gmail_token, args["document_id"], args["find"], args["replace_with"])
    return f"✓ Replaced {result['occurrences_replaced']} occurrence(s)."


# ═══════════════════════════════════════════════════════════════════════════════
#  GOOGLE BUSINESS PROFILE
# ═══════════════════════════════════════════════════════════════════════════════

async def _gbp_accounts(gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import list_accounts
    accounts = list_accounts(gmail_token)
    if not accounts: return "No Business Profile accounts found."
    lines = [f"Found {len(accounts)} account(s):"]
    for a in accounts:
        lines.append(f"• {a['account_name']} ({a['type']}) — {a['name']}")
    return "\n".join(lines)

async def _gbp_locations(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import list_locations
    locations = list_locations(gmail_token, args["account_name"])
    if not locations: return "No locations found."
    lines = [f"Found {len(locations)} location(s):"]
    for loc in locations:
        lines.append(f"• {loc['title']} — {loc['name']}\n  Phone: {loc['phone']} | Website: {loc['website']}")
    return "\n".join(lines)

async def _gbp_reviews(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import get_reviews
    reviews = get_reviews(gmail_token, args["location_name"], args.get("max_results", 10))
    if not reviews: return "No reviews found."
    lines = [f"Found {len(reviews)} review(s):"]
    for r in reviews:
        replied = "✓ replied" if r["replied"] else "⚠ no reply"
        lines.append(f"⭐ {r['rating']} | {r['name']} | {replied}\n  \"{r['comment'][:150]}\"\n  ID: {r['review_id']}")
    return "\n".join(lines)

async def _gbp_reply_review(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import reply_to_review
    return reply_to_review(gmail_token, args["location_name"],
                           args["review_id"], args["reply_text"])

async def _gbp_post(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import create_post
    result = create_post(
        gmail_token, args["location_name"], args["summary"],
        args.get("post_type", "STANDARD"),
        args.get("action_type"), args.get("action_url"),
        args.get("event_title"), args.get("event_start"), args.get("event_end"),
        args.get("offer_coupon"),
    )
    return f"✓ Post created ({result['state']})\nURL: {result.get('search_url', '')}"

async def _gbp_upload_photo(args: dict, gmail_token) -> str:
    if err := _require_google(gmail_token): return err
    from tools.business_profile import upload_photo
    import base64
    image_bytes = base64.b64decode(args["image_base64"])
    result = upload_photo(gmail_token, args["location_name"],
                          image_bytes, args.get("category", "ADDITIONAL"),
                          args.get("description", ""))
    return f"✓ Photo uploaded to Business Profile (category: {result['category']})."