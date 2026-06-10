"""
cortex.tools.gmail
───────────────────
Read-only Gmail tool. Lets agents search and read the user's email using the
shared Google OAuth layer (cortex.integrations.google_auth).

Auth is set up once via `cortex connect gmail`. This tool only reads — it cannot
send. All google-* deps are imported lazily by the auth layer, so cortex works
without them (the tool returns a clear [ERROR] guiding the user to install).
"""

from __future__ import annotations

import base64
from cortex.config import Settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "gmail",
        "description": (
            "Read the user's Gmail (read-only). Use for ANY request about their email/inbox: "
            "find, search, list, summarize or read messages. "
            "action='search': find messages matching a Gmail query (returns id, from, subject, date, snippet). "
            "action='read': get the full text of one message by id. "
            "Gmail query syntax works in 'query' (e.g. 'is:unread', 'from:jefe@x.com', 'subject:factura', 'newer_than:7d')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "read"],
                    "description": "search: find messages. read: fetch one message body by id.",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (action=search). Empty = most recent. "
                                   "Examples: 'is:unread', 'from:alice@x.com newer_than:3d'.",
                },
                "id": {
                    "type": "string",
                    "description": "Message id to read (action=read), as returned by search.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max messages for search (default 10, cap 25).",
                },
            },
            "required": ["action"],
        },
    },
}


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _decode_part(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """Walk the MIME tree, prefer text/plain, fall back to text/html (stripped)."""
    mime = payload.get("mimeType", "")
    body = payload.get("body", {})
    if mime == "text/plain" and body.get("data"):
        return _decode_part(body["data"])
    if mime.startswith("multipart"):
        # First pass: plain text.
        for part in payload.get("parts", []):
            txt = _extract_body(part)
            if txt and part.get("mimeType") == "text/plain":
                return txt
        # Second pass: anything.
        for part in payload.get("parts", []):
            txt = _extract_body(part)
            if txt:
                return txt
    if mime == "text/html" and body.get("data"):
        html = _decode_part(body["data"])
        import re
        return re.sub(r"<[^>]+>", " ", html)
    return ""


def _search(service, query: str, max_results: int) -> str:
    n = max(1, min(int(max_results or 10), 25))
    resp = service.users().messages().list(
        userId="me", q=query or "", maxResults=n
    ).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    if not ids:
        return f"No hay correos que coincidan con: {query or '(recientes)'}"

    lines = [f"{len(ids)} correo(s) — query: {query or '(recientes)'}\n"]
    for mid in ids:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        snippet = msg.get("snippet", "").strip()
        lines.append(
            f"• [{mid}] {_header(headers, 'From')}\n"
            f"    {_header(headers, 'Subject') or '(sin asunto)'}  —  {_header(headers, 'Date')}\n"
            f"    {snippet[:160]}"
        )
    return "\n".join(lines)


def _read(service, mid: str) -> str:
    if not mid:
        return "[ERROR] gmail: falta 'id' para action=read."
    msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    body = _extract_body(payload).strip() or msg.get("snippet", "")
    return (
        f"De: {_header(headers, 'From')}\n"
        f"Para: {_header(headers, 'To')}\n"
        f"Asunto: {_header(headers, 'Subject') or '(sin asunto)'}\n"
        f"Fecha: {_header(headers, 'Date')}\n"
        f"\n{body[:4000]}"
    )


def execute(action: str, query: str = "", id: str = "", max_results: int = 10,
            settings: "Settings | None" = None, **_) -> str:
    from cortex.integrations import google_auth

    cfg = settings or Settings.load()
    try:
        service = google_auth.gmail_service(cfg)
    except google_auth.GoogleAuthError as e:
        return f"[ERROR] {e}"
    except Exception as e:  # network / API build failure
        return f"[ERROR] gmail: {type(e).__name__}: {e}"

    try:
        if action == "search":
            return _search(service, query, max_results)
        if action == "read":
            return _read(service, id)
        return f"[ERROR] gmail: acción desconocida '{action}'. Usa 'search' o 'read'."
    except Exception as e:
        return f"[ERROR] gmail {action}: {type(e).__name__}: {e}"
