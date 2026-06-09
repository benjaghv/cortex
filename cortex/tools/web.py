"""
Web tool — fetch a URL and return text (stripped of HTML).
No API key. Uses httpx. Good for reading docs/pages.
"""

from __future__ import annotations

import re

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web",
        "description": (
            "Fetch a URL over HTTP(S) and return its text content (HTML tags stripped). "
            "Use to read a web page, API response, or online document."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL including http(s)://"},
                "max_chars": {"type": "integer", "description": "Max chars to return. Default 4000."},
            },
            "required": ["url"],
        },
    },
}

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n\s*\n+")


def execute(url: str, max_chars: int = 4000) -> str:
    if not url.startswith(("http://", "https://")):
        return "[ERROR] URL must start with http:// or https://"
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "cortex/0.1"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            text = r.text
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"

    if "html" in ctype:
        text = _TAG.sub("", text)
        text = _HTML.sub(" ", text)
        text = _WS.sub("\n\n", text)
    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… ({len(text) - max_chars} chars truncated)"
    return text or "(empty response)"
