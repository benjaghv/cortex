"""
Search tool — web search via DuckDuckGo (no API key required).
Uses DDG's JSON API for instant answers + HTML scrape for real results.
"""

from __future__ import annotations

import re
import urllib.parse

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "search",
        "description": (
            "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. "
            "Use when you need current information, documentation, news, or anything "
            "that requires searching the internet. Do NOT use filesystem or shell for web info."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "max_results": {"type": "integer", "description": "Max results to return. Default 5."},
            },
            "required": ["query"],
        },
    },
}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

_TAGS    = re.compile(r"<[^>]+>")
_SPACES  = re.compile(r"\s+")
_RESULT  = re.compile(
    r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(.*?)</(?:a|span)>',
    re.DOTALL,
)


def _strip(html: str) -> str:
    text = _TAGS.sub(" ", html)
    text = _SPACES.sub(" ", text)
    return text.strip()


def execute(query: str, max_results: int = 5) -> str:
    max_results = min(max_results, 10)

    # 1. Try DDG instant answer API first (structured, fast)
    try:
        with httpx.Client(timeout=10.0, headers=_HEADERS, follow_redirects=True) as c:
            params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
            r = c.get("https://api.duckduckgo.com/", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"[ERROR] Search request failed: {e}"

    results: list[str] = []

    # Instant answer
    if data.get("AbstractText"):
        source = data.get("AbstractSource", "")
        url = data.get("AbstractURL", "")
        results.append(f"**{source}**: {data['AbstractText']}\n  {url}")

    # Related topics
    for topic in data.get("RelatedTopics", [])[:max_results]:
        if isinstance(topic, dict) and topic.get("Text"):
            url = topic.get("FirstURL", "")
            results.append(f"- {topic['Text']}\n  {url}")

    if results:
        return f"Search results for: **{query}**\n\n" + "\n\n".join(results[:max_results])

    # 2. Fallback: DDG HTML lite
    try:
        with httpx.Client(timeout=12.0, headers=_HEADERS, follow_redirects=True) as c:
            r = c.get("https://html.duckduckgo.com/html/", params={"q": query, "kl": "wt-wt"})
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return f"[ERROR] Fallback search failed: {e}"

    matches = _RESULT.findall(html)
    if not matches:
        return f"No results found for: {query}"

    lines = [f"Search results for: **{query}**\n"]
    count = 0
    for href, title, snippet in matches:
        # Skip DDG ads (redirect via y.js with ad_provider/ad_domain)
        if "ad_provider=" in href or "ad_domain=" in href or "/y.js?" in href:
            continue
        # DDG wraps real URLs in a redirect — extract uddg param
        if "uddg=" in href:
            url = urllib.parse.unquote(href.split("uddg=")[-1].split("&")[0])
        else:
            url = href
        count += 1
        lines.append(f"**{count}. {_strip(title)}**")
        lines.append(f"   {_strip(snippet)}")
        lines.append(f"   {url}")
        lines.append("")
        if count >= max_results:
            break

    if count == 0:
        return f"No organic results found for: {query} (only ads returned)."
    return "\n".join(lines)
