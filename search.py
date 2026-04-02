"""
tools/search.py — Free web search using DuckDuckGo.

No API key needed. No cost. Uses the same approach as the JS version:
  1. Try DuckDuckGo Instant Answer API (best for factual queries)
  2. Fall back to scraping DuckDuckGo HTML results (best for general queries)

This is called by executor.py when the LLM calls the web_search tool.
"""

from __future__ import annotations
import httpx
import re


def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web using DuckDuckGo. Free, no API key required.
    Returns a formatted string of results for the LLM to read.
    """
    results = _ddg_instant(query, max_results)

    # If instant API gave nothing useful, fall back to HTML scrape
    if not results:
        results = _ddg_html(query, max_results)

    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        if r.get("description"):
            lines.append(f"   {r['description'][:200]}")
        lines.append("")

    return "\n".join(lines)


def _ddg_instant(query: str, max_results: int) -> list[dict]:
    """
    DuckDuckGo Instant Answer API — great for facts, definitions, quick answers.
    Returns [] if nothing useful found.
    """
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q":              query,
                    "format":         "json",
                    "no_html":        "1",
                    "skip_disambig":  "1",
                },
                headers={"User-Agent": "AI-Agent/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []

        # Main abstract (Wikipedia-style answer)
        if data.get("Abstract"):
            results.append({
                "title":       data.get("Heading", query),
                "url":         data.get("AbstractURL", ""),
                "description": data["Abstract"],
            })

        # Related topics
        for topic in (data.get("RelatedTopics") or []):
            if len(results) >= max_results:
                break
            if topic.get("Text") and topic.get("FirstURL"):
                title = topic["Text"].split(" - ")[0]
                results.append({
                    "title":       title,
                    "url":         topic["FirstURL"],
                    "description": topic["Text"],
                })

        return results

    except Exception as e:
        print(f"[search] instant API error: {e}")
        return []


def _ddg_html(query: str, max_results: int) -> list[dict]:
    """
    Scrape DuckDuckGo HTML search page — works for any query.
    No API key, no cost, no rate limits (within reason).
    """
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; AI-Agent/1.0)",
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            resp.raise_for_status()
            html = resp.text

        results = []

        # Extract result titles + URLs
        title_matches = re.findall(
            r'class="result__title"[^>]*>.*?<a[^>]+href="([^"]*)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        # Extract snippets
        snippet_matches = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
            html, re.DOTALL
        )

        for i, (url, title) in enumerate(title_matches[:max_results]):
            # Clean HTML tags from title/snippet
            clean_title   = re.sub(r'<[^>]+>', '', title).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', snippet_matches[i]).strip() if i < len(snippet_matches) else ""

            # Decode DDG redirect URLs
            if url.startswith("/l/?"):
                url_match = re.search(r'uddg=([^&]+)', url)
                if url_match:
                    from urllib.parse import unquote
                    url = unquote(url_match.group(1))

            if clean_title:
                results.append({
                    "title":       clean_title,
                    "url":         url,
                    "description": clean_snippet,
                })

        return results

    except Exception as e:
        print(f"[search] html scrape error: {e}")
        return []