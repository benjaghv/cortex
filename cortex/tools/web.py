"""
Web tool — fetch a URL and return text (stripped of HTML).
No API key. Uses httpx. Good for reading docs/pages.
"""

from __future__ import annotations

import html
import re

import httpx

SCHEMA = {
    "type": "function",
    "function": {
        "name": "web",
        "description": (
            "Fetch a URL and return its text content. Use for any website, webpage, or online document. "
            "If the user mentions a domain or URL, call this tool immediately with https:// prefix."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL including http(s)://"},
                "max_chars": {"type": "integer", "description": "Max chars to return. Default 6000."},
            },
            "required": ["url"],
        },
    },
}

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n\s*\n+")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

_BINARY_TYPES = ("application/pdf", "application/msword", "application/vnd", "image/", "audio/", "video/")


def execute(url: str, max_chars: int = 6000) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url  # auto-prefix if missing
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as c:
            r = c.get(url, headers=_HEADERS)
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").lower()
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"

    # Binary file — can't extract text
    if any(b in ctype for b in _BINARY_TYPES):
        fname = url.split("/")[-1] or url
        return (
            f"[BINARY FILE] '{fname}' is a binary file ({ctype}) — cannot read as text. "
            f"To read a .docx or .pdf, it must be downloaded locally first, "
            f"then opened with a specialized tool."
        )

    text = r.text
    if "html" in ctype or "<html" in text[:200].lower():
        text = _TAG.sub("", text)
        text = _HTML.sub(" ", text)
        text = html.unescape(text)   # &lt; → <, &amp; → &, etc.
        text = _WS.sub("\n\n", text)

    text = text.strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n… ({len(text) - max_chars} chars truncated)"
    return text or "(empty page)"
