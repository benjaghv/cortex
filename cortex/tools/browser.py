"""
cortex.tools.browser
─────────────────────
Real browser tool powered by Playwright (headless Chromium).
Handles JavaScript-heavy sites, SPAs, job boards, etc.

Install once:
    pip install playwright
    playwright install chromium

Actions:
    fetch  — load a URL, return text + links
    search — load URL, type a query into a search box, return results
"""

from __future__ import annotations

import re

SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser",
        "description": (
            "Navigate web pages with a REAL browser (renders JavaScript). "
            "Use this for job boards (LinkedIn, Indeed, Trabajando.cl), social media, "
            "SPAs, or any site that fails with the plain web tool. "
            "action='fetch': load a URL, return text + links. "
            "action='search': navigate to a URL and type a query in the search box. "
            "Prefer constructing search URLs directly when possible "
            "(e.g. https://cl.indeed.com/jobs?q=ingeniero&l=Santiago)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["fetch", "search"],
                    "description": "fetch: load URL and extract content. search: fill search box then extract.",
                },
                "url": {
                    "type": "string",
                    "description": "Full URL to navigate to (include https://).",
                },
                "query": {
                    "type": "string",
                    "description": "Search text to type (only for action=search).",
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the search input (leave empty to auto-detect).",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max text chars to return (default 5000).",
                },
                "max_links": {
                    "type": "integer",
                    "description": "Max links to include (default 25).",
                },
            },
            "required": ["action", "url"],
        },
    },
}

# Common search input selectors, tried in order
_SEARCH_SELECTORS = [
    "input[type='search']",
    "input[name='q']",
    "input[name='query']",
    "input[name='search']",
    "input[name='keywords']",
    "input[id*='search']",
    "input[placeholder*='search' i]",
    "input[placeholder*='buscar' i]",
    "input[placeholder*='keyword' i]",
    "input[aria-label*='search' i]",
    "input[type='text']",
]


def _clean_text(raw: str) -> str:
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def execute(
    action: str,
    url: str,
    query: str = "",
    selector: str = "",
    max_chars: int = 5000,
    max_links: int = 25,
    **_,
) -> str:
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return (
            "[ERROR] Playwright not installed.\n"
            "Run: pip install playwright && playwright install chromium"
        )

    if not url.startswith("http"):
        url = "https://" + url

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="es-CL",
            )
            page = ctx.new_page()

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except PWTimeout:
                pass  # proceed anyway — partial load is often enough

            if action == "search" and query:
                selectors = [selector] if selector else _SEARCH_SELECTORS
                filled = False
                for sel in selectors:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            el.fill(query)
                            el.press("Enter")
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=10_000)
                            except PWTimeout:
                                pass
                            filled = True
                            break
                    except Exception:
                        continue
                if not filled:
                    return (
                        f"[ERROR] No search input found on {url}. "
                        "Try constructing the search URL directly with query params."
                    )

            # Wait a bit for dynamic content
            try:
                page.wait_for_timeout(1500)
            except Exception:
                pass

            # ── Extract text ──────────────────────────────────────────────
            try:
                text = page.inner_text("body") or ""
            except Exception:
                text = ""
            text = _clean_text(text)[:max_chars]

            # ── Extract links ─────────────────────────────────────────────
            links: list[str] = []
            try:
                for a in page.query_selector_all("a[href]"):
                    try:
                        href = (a.get_attribute("href") or "").strip()
                        label = _clean_text(a.inner_text() or "")[:100]
                        if href.startswith("http") and label and len(label) > 2:
                            links.append(f"- {label}: {href}")
                            if len(links) >= max_links:
                                break
                    except Exception:
                        continue
            except Exception:
                pass

            browser.close()

            result = text
            if links:
                result += f"\n\n--- LINKS ({len(links)}) ---\n" + "\n".join(links)
            return result or "(page loaded but no text extracted)"

    except Exception as e:
        return f"[ERROR] Browser: {type(e).__name__}: {e}"
