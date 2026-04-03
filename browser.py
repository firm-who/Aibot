"""
tools/browser.py — Cloud browser automation using Playwright + Browserbase.

Full flow:
  1. Open URL in Browserbase cloud browser
  2. Extract page HTML/structure
  3. Use LLM to decide what to fill/click
  4. Fill the form fields
  5. Screenshot the filled form
  6. Send screenshot to user via Telegram and PAUSE
  7. If user says yes → submit. If no → cancel.
"""

from __future__ import annotations
import os
import base64
import json
import httpx
from playwright.async_api import async_playwright, Page



# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════════

async def _create_session(user_config: dict) -> str:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://www.browserbase.com/v1/sessions",
            headers={
                "x-bb-api-key":  user_config["browserbase_api_key"],
                "Content-Type":  "application/json",
            },
            json={"projectId": user_config["browserbase_project_id"]},
        )
        resp.raise_for_status()
        return resp.json()["id"]


def _cdp_url(session_id: str, user_config: dict = {}) -> str:
    return (
        f"wss://connect.browserbase.com?"
        f"apiKey={user_config.get('browserbase_api_key','')}&sessionId={session_id}"
    )


async def _screenshot_b64(page: Page) -> str:
    shot = await page.screenshot(full_page=False)
    return base64.b64encode(shot).decode()


# ═══════════════════════════════════════════════════════════════════════════════
#  PAGE CONTENT EXTRACTION  (for LLM to understand the form)
# ═══════════════════════════════════════════════════════════════════════════════

async def _extract_form_structure(page: Page) -> str:
    """Extract all input fields, labels, buttons from page for LLM to read."""
    return await page.evaluate("""
        () => {
            const fields = [];

            // All inputs
            document.querySelectorAll('input, textarea, select').forEach(el => {
                const label = document.querySelector(`label[for="${el.id}"]`);
                fields.push({
                    tag:         el.tagName.toLowerCase(),
                    type:        el.type || 'text',
                    id:          el.id || '',
                    name:        el.name || '',
                    placeholder: el.placeholder || '',
                    label:       label ? label.innerText.trim() : '',
                    value:       el.value || '',
                    required:    el.required,
                    options:     el.tagName === 'SELECT'
                                   ? Array.from(el.options).map(o => o.text)
                                   : [],
                });
            });

            // Buttons
            const buttons = [];
            document.querySelectorAll('button, input[type="submit"]').forEach(b => {
                buttons.push(b.innerText?.trim() || b.value || b.type);
            });

            return JSON.stringify({ fields, buttons, title: document.title });
        }
    """)


# ═══════════════════════════════════════════════════════════════════════════════
#  LLM-GUIDED FORM FILLING
# ═══════════════════════════════════════════════════════════════════════════════

async def _llm_decide_actions(page_structure: str, instruction: str,
                               user_config: dict) -> list[dict]:
    """
    Ask the LLM: given this page structure and instruction,
    return a list of fill/click actions as JSON.
    """
    import llm as llm_module

    prompt = f"""You are controlling a web browser. 
    
The user wants to: {instruction}

Here is the page structure (form fields and buttons):
{page_structure}

Return ONLY a valid JSON array of actions. Each action is one of:

  Fill an input:
  {{"action": "fill", "selector": "#id or [name='x']", "value": "text to type"}}

  Select a dropdown option:
  {{"action": "select", "selector": "#id or [name='x']", "value": "option text"}}

  Check a checkbox:
  {{"action": "check", "selector": "#id or [name='x']"}}

Do NOT include a submit action — we will ask the user before submitting.
Return ONLY the JSON array, no explanation."""

    result = llm_module.call_llm(
        user={},
        user_config=user_config,
        conversation_history=[{"role": "user", "content": prompt}],
        memory_context="",
    )

    text = result.get("text", "[]").strip()
    # Strip markdown code fences if LLM wraps in ```json
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        return []


async def _apply_actions(page: Page, actions: list[dict]) -> list[str]:
    """Apply fill/select/check actions to the page. Returns log of what was done."""
    log = []
    for action in actions:
        try:
            sel = action.get("selector", "")
            if not sel:
                continue

            if action["action"] == "fill":
                await page.fill(sel, action["value"])
                log.append(f"Filled '{sel}' with '{action['value']}'")

            elif action["action"] == "select":
                await page.select_option(sel, label=action["value"])
                log.append(f"Selected '{action['value']}' in '{sel}'")

            elif action["action"] == "check":
                await page.check(sel)
                log.append(f"Checked '{sel}'")

        except Exception as e:
            log.append(f"Could not act on '{sel}': {e}")

    return log


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINTS
# ═══════════════════════════════════════════════════════════════════════════════

async def browse_url(url: str, instruction: str,
                     user_config: dict,
                     screenshot_before_submit: bool = True) -> dict:
    """
    Open URL, fill the form using LLM guidance, screenshot, and pause for confirmation.

    Returns:
        {
            "success": bool,
            "result": str,
            "screenshot_base64": str | None,
            "needs_confirmation": bool,
            "session_id": str | None,
            "fill_log": list[str],
        }
    """
    if not user_config.get('browserbase_api_key'):
        return {
            "success": False,
            "result": "Browser tool not configured. Set BROWSERBASE_API_KEY.",
            "screenshot_base64": None,
            "needs_confirmation": False,
            "session_id": None,
            "fill_log": [],
        }

    try:
        session_id = await _create_session(user_config)
        cdp_url    = _cdp_url(session_id, user_config)

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(cdp_url)
            ctx     = browser.contexts[0]
            page    = ctx.pages[0] if ctx.pages else await ctx.new_page()

            # 1. Navigate
            await page.goto(url, wait_until="networkidle", timeout=30000)
            title = await page.title()

            # 2. Extract form structure
            page_structure = await _extract_form_structure(page)

            # 3. Ask LLM what to fill
            actions = await _llm_decide_actions(page_structure, instruction, user_config)

            # 4. Fill the form
            fill_log = await _apply_actions(page, actions)

            # 5. Screenshot the filled form
            await page.wait_for_timeout(500)  # brief pause for UI to settle
            screenshot_b64 = await _screenshot_b64(page)

            # Detect if this needs confirmation before submitting
            submit_keywords = ["submit", "order", "buy", "purchase",
                               "confirm", "send", "pay", "checkout", "book"]
            needs_confirmation = screenshot_before_submit and any(
                k in instruction.lower() for k in submit_keywords
            )

            # Keep session alive — don't close browser yet if confirming
            if not needs_confirmation:
                await browser.close()
                session_id = None

        filled_summary = "\n".join(fill_log) if fill_log else "No fields filled."
        return {
            "success":            True,
            "result":             f"Opened '{title}'. Filled form.\n{filled_summary}",
            "screenshot_base64":  screenshot_b64,
            "needs_confirmation": needs_confirmation,
            "session_id":         session_id,
            "fill_log":           fill_log,
        }

    except Exception as e:
        return {
            "success":            False,
            "result":             f"Browser error: {str(e)}",
            "screenshot_base64":  None,
            "needs_confirmation": False,
            "session_id":         None,
            "fill_log":           [],
        }


async def submit_form(session_id: str, user_config: dict = {}) -> dict:
    """
    Called after user confirms — reconnect to the live session and submit.
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(_cdp_url(session_id, user_config))
            ctx     = browser.contexts[0]
            page    = ctx.pages[0]

            # Try common submit selectors
            submitted = False
            for selector in [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Submit')",
                "button:has-text('Confirm')",
                "button:has-text('Send')",
                "button:has-text('Order')",
                "button:has-text('Buy')",
                "button:has-text('Book')",
            ]:
                try:
                    await page.click(selector, timeout=2000)
                    submitted = True
                    break
                except Exception:
                    continue

            if not submitted:
                # Last resort: press Enter
                await page.keyboard.press("Enter")

            await page.wait_for_load_state("networkidle", timeout=15000)
            title          = await page.title()
            screenshot_b64 = await _screenshot_b64(page)
            await browser.close()

            return {
                "success":           True,
                "result":            f"Form submitted! Current page: {title}",
                "screenshot_base64": screenshot_b64,
            }

    except Exception as e:
        return {
            "success":           False,
            "result":            f"Submit failed: {str(e)}",
            "screenshot_base64": None,
        }


async def cancel_session(session_id: str) -> None:
    """Close a Browserbase session without submitting."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(_cdp_url(session_id))
            await browser.close()
    except Exception:
        pass