"""
cortex.tools.gmail
───────────────────
Gmail tool. Search and read email; send, draft, and trash with a HUMAN
confirmation gate on the risky actions.

Auth is set up once via `cortex connect gmail` (OAuth). Scopes:
  - gmail.readonly  → search / read
  - gmail.send      → send / draft
  - gmail.modify    → trash (recoverable; permanent delete is NOT granted)

Safety model:
  - search / read / draft  → no confirmation (no irreversible effect).
  - send / trash           → display.confirm_action() asks the user y/N in the
                             terminal. The LLM cannot bypass this — it's answered
                             by the human, not the model. Deny on EOF/Ctrl-C.

All google-* deps are imported lazily by the auth layer, so cortex works without
them (the tool returns a clear [ERROR] guiding the user to reinstall).
"""

from __future__ import annotations

import base64
from cortex.config import Settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "gmail",
        "description": (
            "Read AND manage the user's Gmail. Use for ANY request about their email/inbox.\n"
            "action='search': find messages (returns id, from, subject, date, snippet).\n"
            "action='read': full text of one message by id.\n"
            "action='send': send a new email (to, subject, body). Asks the user to confirm first.\n"
            "action='draft': save a draft without sending (to, subject, body). No confirmation.\n"
            "action='trash': move message(s) to Trash (recoverable). Pass 'id' for one OR 'ids' "
            "(a list) for many. To delete SEVERAL emails, make ONE call with ALL ids in 'ids' — "
            "NEVER loop trash one id at a time. One confirmation covers the whole batch.\n"
            "Gmail query syntax works in 'query' (e.g. 'is:unread', 'from:x@y.com', 'newer_than:7d')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "read", "send", "draft", "trash"],
                    "description": "search/read (safe) · send/draft · trash (recoverable).",
                },
                "query": {
                    "type": "string",
                    "description": "Gmail search query (action=search). Empty = most recent.",
                },
                "id": {
                    "type": "string",
                    "description": "Single message id (action=read, or trash one).",
                },
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of message ids to trash in ONE batch (action=trash). "
                                   "Use this to delete multiple emails with a single confirmation.",
                },
                "to": {
                    "type": "string",
                    "description": "Recipient email address (action=send or draft). "
                                   "Multiple: comma-separated.",
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject (action=send or draft).",
                },
                "body": {
                    "type": "string",
                    "description": "Email body, plain text (action=send or draft).",
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


# ── header / body helpers ─────────────────────────────────────────────────────────────

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
        for part in payload.get("parts", []):
            txt = _extract_body(part)
            if txt and part.get("mimeType") == "text/plain":
                return txt
        for part in payload.get("parts", []):
            txt = _extract_body(part)
            if txt:
                return txt
    if mime == "text/html" and body.get("data"):
        html = _decode_part(body["data"])
        import re
        return re.sub(r"<[^>]+>", " ", html)
    return ""


# ── read-only actions ─────────────────────────────────────────────────────────────────

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


# ── write actions ──────────────────────────────────────────────────────────────────────

def _build_raw(to: str, subject: str, body: str) -> str:
    """Build a base64url-encoded RFC 2822 message."""
    from email.mime.text import MIMEText
    msg = MIMEText(body, _charset="utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")


def _confirm(title: str, lines: "list[str]", danger: bool = False) -> bool:
    """Ask the human. Falls back to deny-safe if display can't be imported."""
    try:
        from cortex.display import confirm_action
        return confirm_action(title, lines, danger=danger)
    except Exception:
        return False


def _send(service, to: str, subject: str, body: str) -> str:
    if not to:
        return "[ERROR] gmail: falta 'to' (destinatario) para enviar."
    if not _confirm(
        "✉ Enviar correo",
        [f"Para:    {to}",
         f"Asunto:  {subject or '(sin asunto)'}",
         "",
         (body[:500] + ("…" if len(body) > 500 else "")) or "(cuerpo vacío)"],
    ):
        return "[CANCELLED] Envío cancelado por el usuario. No se envió nada."
    raw = _build_raw(to, subject, body)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return f"✓ Correo enviado a {to} (id: {sent.get('id', '?')})."


def _draft(service, to: str, subject: str, body: str) -> str:
    if not to:
        return "[ERROR] gmail: falta 'to' (destinatario) para el borrador."
    raw = _build_raw(to, subject, body)
    d = service.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
    return (f"✓ Borrador guardado (id: {d.get('id', '?')}) para {to}. "
            "No se ha enviado — revísalo en Gmail.")


def _fetch_summary(service, mid: str) -> "str | None":
    """Short 'From — Subject' for one id. Returns None if the id doesn't exist
    (e.g. the model hallucinated it) — caller treats that as invalid."""
    try:
        msg = service.users().messages().get(
            userId="me", id=mid, format="metadata",
            metadataHeaders=["From", "Subject"],
        ).execute()
    except Exception:
        return None
    headers = msg.get("payload", {}).get("headers", [])
    frm = _header(headers, "From") or "?"
    if "<" in frm:
        frm = frm.split("<")[0].strip().strip('"') or frm
    subj = _header(headers, "Subject") or "(sin asunto)"
    return f"{frm[:28]:<28} {subj[:42]}"


def _trash(service, mid: str, ids: "list[str] | None") -> str:
    # Normalize to a list of ids.
    targets: list[str] = []
    if ids:
        targets = [str(i).strip() for i in ids if str(i).strip()]
    elif mid:
        targets = [mid.strip()]
    if not targets:
        return "[ERROR] gmail: falta 'id' o 'ids' para enviar a la papelera."

    # Validate EVERY id by fetching its summary. Invalid ids = the model invented
    # them instead of searching first. Refuse rather than fake a success on nothing.
    summaries: list[str] = []
    valid: list[str] = []
    invalid: list[str] = []
    for t in targets:
        s = _fetch_summary(service, t)
        if s is None:
            invalid.append(t)
        else:
            valid.append(t)
            summaries.append(s)

    if not valid:
        return (
            "[ERROR] Ninguno de esos ids existe en el buzón. "
            "Probablemente no buscaste primero. Usa gmail action='search' para obtener "
            "ids reales y reintenta el trash con esos ids."
        )
    if invalid:
        # Don't silently drop — tell the model so it can re-search.
        return (
            f"[ERROR] {len(invalid)} de {len(targets)} ids no existen "
            f"(inválidos: {', '.join(invalid[:5])}{'…' if len(invalid) > 5 else ''}). "
            "Haz gmail action='search' para obtener los ids reales antes de borrar."
        )

    # Build ONE confirmation listing every (verified) message.
    if len(valid) == 1:
        title = "🗑 Mover a la papelera"
        lines = [summaries[0], "", "(recuperable 30 días)"]
    else:
        title = f"🗑 Mover {len(valid)} correos a la papelera"
        lines = [f"  • {s}" for s in summaries] + ["", "(recuperables 30 días)"]

    if not _confirm(title, lines):
        return f"[CANCELLED] Operación cancelada. No se movió ninguno de los {len(valid)} correos."

    # Execute. batchModify trashes many in a single API call; trash() for one.
    try:
        if len(valid) > 1:
            service.users().messages().batchModify(
                userId="me", body={"ids": valid, "addLabelIds": ["TRASH"]}
            ).execute()
        else:
            service.users().messages().trash(userId="me", id=valid[0]).execute()
    except Exception as e:
        return f"[ERROR] gmail trash: {type(e).__name__}: {e}"

    # Verify it actually landed in Trash (catches silent scope/label failures).
    try:
        check = service.users().messages().get(
            userId="me", id=valid[0], format="metadata", metadataHeaders=[],
        ).execute()
        if "TRASH" not in check.get("labelIds", []):
            return ("[ERROR] La API no devolvió error pero el correo NO quedó en la papelera. "
                    "Puede faltar el permiso 'gmail.modify' — reconéctate: cortex connect gmail")
    except Exception:
        pass  # verification is best-effort

    return f"✓ {len(valid)} correo(s) movido(s) a la papelera (recuperables 30 días)."


# ── entrypoint ─────────────────────────────────────────────────────────────────────────

def execute(action: str, query: str = "", id: str = "", ids: "list | None" = None,
            to: str = "", subject: str = "", body: str = "", max_results: int = 10,
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
        if action == "send":
            return _send(service, to, subject, body)
        if action == "draft":
            return _draft(service, to, subject, body)
        if action == "trash":
            return _trash(service, id, ids)
        return (f"[ERROR] gmail: acción desconocida '{action}'. "
                "Usa search, read, send, draft o trash.")
    except Exception as e:
        return f"[ERROR] gmail {action}: {type(e).__name__}: {e}"
