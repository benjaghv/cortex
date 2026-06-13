"""
cortex.tools.outlook
──────────────────────
Outlook / Microsoft 365 email via Microsoft Graph. Mirror of the gmail tool:
search, read, send, draft, and trash (move to Deleted Items, recoverable).

Auth is set up once via `cortex connect outlook` (device-code OAuth). Uses the
shared cortex.integrations.microsoft_auth layer.

Safety model (same as gmail):
  - search / read / draft  → no confirmation.
  - send / trash           → display.confirm_action() asks the user y/N in the
                             terminal. The LLM cannot bypass it.
"""

from __future__ import annotations

import re
from cortex.config import Settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "outlook",
        "description": (
            "Read AND manage the user's Outlook / Microsoft 365 email. Use for ANY request "
            "about their Outlook inbox/correo.\n"
            "action='search': find messages (returns id, from, subject, date, preview).\n"
            "action='read': full body of one message by id.\n"
            "action='send': send a new email (to, subject, body). Asks the user to confirm first.\n"
            "action='draft': save a draft without sending (to, subject, body). No confirmation.\n"
            "action='trash': move message(s) to Deleted Items — pass 'id' for one OR 'ids' (list) "
            "for many. Asks the user to confirm first. To delete SEVERAL, use ONE call with all ids."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "read", "send", "draft", "trash"],
                },
                "query": {
                    "type": "string",
                    "description": "Search text (action=search). Empty = most recent. "
                                   "Matches subject/sender/body.",
                },
                "id": {"type": "string", "description": "Message id (action=read or trash one)."},
                "ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "List of message ids to trash in ONE batch (action=trash).",
                },
                "to": {"type": "string",
                       "description": "Recipient(s), comma-separated (action=send or draft)."},
                "subject": {"type": "string", "description": "Subject (action=send or draft)."},
                "body": {"type": "string", "description": "Body text (action=send or draft)."},
                "max_results": {"type": "integer",
                                "description": "Max messages for search (default 10, cap 25)."},
            },
            "required": ["action"],
        },
    },
}


def _confirm(title: str, lines: "list[str]", danger: bool = False) -> bool:
    try:
        from cortex.display import confirm_action
        return confirm_action(title, lines, danger=danger)
    except Exception:
        return False


def _strip_html(html: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _addr(m: dict) -> str:
    e = (m or {}).get("emailAddress", {})
    return e.get("address") or e.get("name") or "?"


# ── read-only ──────────────────────────────────────────────────────────────────────────

def _search(cfg, query: str, max_results: int) -> str:
    from cortex.integrations import microsoft_auth as ms
    n = max(1, min(int(max_results or 10), 25))
    params = {
        "$top": n,
        "$select": "id,subject,from,receivedDateTime,bodyPreview",
        "$orderby": "receivedDateTime desc",
    }
    headers = None
    if query:
        # $search can't combine with $orderby; use $search alone.
        params = {"$top": n, "$select": "id,subject,from,receivedDateTime,bodyPreview",
                  "$search": f'"{query}"'}
        headers = {"ConsistencyLevel": "eventual"}
    # Scope to the Inbox folder ONLY. The whole-mailbox /me/messages also returns
    # Sticky Notes (Notas rápidas) and other non-email items synced into the mailbox.
    data = ms.graph_get(cfg, "/me/mailFolders/inbox/messages", params=params, headers=headers)
    msgs = data.get("value", [])
    if not msgs:
        return f"No hay correos que coincidan con: {query or '(recientes)'}"
    lines = [f"{len(msgs)} correo(s) — {query or '(recientes)'}\n"]
    for m in msgs:
        lines.append(
            f"• [{m.get('id')}] {_addr(m.get('from'))}\n"
            f"    {m.get('subject') or '(sin asunto)'}  —  {m.get('receivedDateTime', '')}\n"
            f"    {(m.get('bodyPreview') or '').strip()[:160]}"
        )
    return "\n".join(lines)


def _read(cfg, mid: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not mid:
        return "[ERROR] outlook: falta 'id' para action=read."
    m = ms.graph_get(cfg, f"/me/messages/{mid}",
                     params={"$select": "subject,from,toRecipients,receivedDateTime,body"})
    body = m.get("body", {})
    content = body.get("content", "")
    if body.get("contentType", "").lower() == "html":
        content = _strip_html(content)
    to = ", ".join(_addr(r) for r in m.get("toRecipients", []))
    return (
        f"De: {_addr(m.get('from'))}\n"
        f"Para: {to}\n"
        f"Asunto: {m.get('subject') or '(sin asunto)'}\n"
        f"Fecha: {m.get('receivedDateTime', '')}\n"
        f"\n{content[:4000]}"
    )


# ── write ────────────────────────────────────────────────────────────────────────────────

def _recipients(to: str) -> list:
    out = []
    for addr in re.split(r"[,;]\s*", to.strip()):
        if addr:
            out.append({"emailAddress": {"address": addr}})
    return out


def _send(cfg, to: str, subject: str, body: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not to:
        return "[ERROR] outlook: falta 'to' (destinatario)."
    if not _confirm("✉ Enviar correo (Outlook)",
                    [f"Para:    {to}", f"Asunto:  {subject or '(sin asunto)'}", "",
                     (body[:500] + ("…" if len(body) > 500 else "")) or "(cuerpo vacío)"]):
        return "[CANCELLED] Envío cancelado por el usuario. No se envió nada."
    msg = {"message": {"subject": subject,
                       "body": {"contentType": "Text", "content": body},
                       "toRecipients": _recipients(to)},
           "saveToSentItems": True}
    ms.graph_post(cfg, "/me/sendMail", json_body=msg)
    return f"✓ Correo enviado a {to}."


def _draft(cfg, to: str, subject: str, body: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    msg = {"subject": subject, "body": {"contentType": "Text", "content": body},
           "toRecipients": _recipients(to) if to else []}
    d = ms.graph_post(cfg, "/me/messages", json_body=msg)
    return f"✓ Borrador guardado (id: {d.get('id', '?')}). No se ha enviado — revísalo en Outlook."


def _summary(cfg, mid: str) -> "str | None":
    from cortex.integrations import microsoft_auth as ms
    try:
        m = ms.graph_get(cfg, f"/me/messages/{mid}", params={"$select": "from,subject"})
    except Exception:
        return None
    return f"{_addr(m.get('from')):<28} {(m.get('subject') or '(sin asunto)')[:42]}"


def _trash(cfg, mid: str, ids: "list | None") -> str:
    from cortex.integrations import microsoft_auth as ms
    targets = [str(i).strip() for i in (ids or []) if str(i).strip()] or ([mid.strip()] if mid else [])
    if not targets:
        return "[ERROR] outlook: falta 'id' o 'ids' para mover a Eliminados."

    summaries, valid, invalid = [], [], []
    for t in targets:
        s = _summary(cfg, t)
        (invalid if s is None else valid).append(t)
        if s is not None:
            summaries.append(s)
    if not valid:
        return ("[ERROR] Ninguno de esos ids existe. Probablemente no buscaste primero. "
                "Usa action='search' para obtener ids reales.")
    if invalid:
        return (f"[ERROR] {len(invalid)} de {len(targets)} ids no existen. "
                "Haz action='search' para obtener los ids reales antes de borrar.")

    if len(valid) == 1:
        title, lines = "🗑 Mover a Eliminados", [summaries[0], "", "(recuperable en Outlook)"]
    else:
        title = f"🗑 Mover {len(valid)} correos a Eliminados"
        lines = [f"  • {s}" for s in summaries] + ["", "(recuperables en Outlook)"]
    if not _confirm(title, lines):
        return f"[CANCELLED] Operación cancelada. No se movió ninguno de los {len(valid)} correos."

    try:
        for t in valid:
            ms.graph_post(cfg, f"/me/messages/{t}/move",
                          json_body={"destinationId": "deleteditems"})
    except Exception as e:
        return f"[ERROR] outlook trash: {type(e).__name__}: {e}"
    return f"✓ {len(valid)} correo(s) movido(s) a Eliminados (recuperables en Outlook)."


# ── entrypoint ─────────────────────────────────────────────────────────────────────────

def execute(action: str, query: str = "", id: str = "", ids: "list | None" = None,
            to: str = "", subject: str = "", body: str = "", max_results: int = 10,
            settings: "Settings | None" = None, **_) -> str:
    from cortex.integrations import microsoft_auth as ms
    cfg = settings or Settings.load()
    try:
        if action == "search":
            return _search(cfg, query, max_results)
        if action == "read":
            return _read(cfg, id)
        if action == "send":
            return _send(cfg, to, subject, body)
        if action == "draft":
            return _draft(cfg, to, subject, body)
        if action == "trash":
            return _trash(cfg, id, ids)
        return (f"[ERROR] outlook: acción desconocida '{action}'. "
                "Usa search, read, send, draft o trash.")
    except ms.MicrosoftAuthError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        msg = str(e)
        if "401" in msg or "InvalidAuthenticationToken" in msg:
            return "[ERROR] outlook: sesión expirada o sin permisos. Reconéctate: cortex connect outlook"
        return f"[ERROR] outlook {action}: {type(e).__name__}: {e}"
