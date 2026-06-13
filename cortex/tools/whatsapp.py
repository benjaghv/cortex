"""
cortex.tools.whatsapp
──────────────────────
Drive YOUR own WhatsApp Web session like a normal user: open chats, read them,
type and send messages, attach files.

Design (safer than a headless bot):
  - A PERSISTENT, VISIBLE Chromium profile at ~/.cortex/whatsapp_profile.
  - You scan the QR ONCE; the session stays logged in across runs.
  - The window stays open and cortex navigates it with human-paced clicks/typing,
    exactly like you would — you can watch every action (Cowork-style).
  - Sending a message or file ALWAYS asks you to confirm first (y/N gate).

Reuses the Playwright dependency from the browser tool:
    pip install playwright && playwright install chromium

⚠ Automating WhatsApp Web is unofficial. Keep volume low and human-like. Prefer a
secondary number (WhatsApp Business) so your main account is never at risk.
"""

from __future__ import annotations

import os
from pathlib import Path

from cortex.config import CONFIG_DIR

_PROFILE_DIR = CONFIG_DIR / "whatsapp_profile"
_WA_URL = "https://web.whatsapp.com"

SCHEMA = {
    "type": "function",
    "function": {
        "name": "whatsapp",
        "description": (
            "Control the user's own WhatsApp Web to read and send messages, like a normal user. "
            "A visible browser stays logged in (QR scanned once).\n"
            "action='status': check if logged in (prompts QR scan if not).\n"
            "action='list': list recent chats in the sidebar.\n"
            "action='read': open a chat by contact/group name and return the latest messages.\n"
            "action='send': open a chat and send a text message — asks the user to confirm first.\n"
            "action='send_file': open a chat and send a file (path) — asks the user to confirm first.\n"
            "action='close': close the WhatsApp browser window."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "list", "read", "send", "send_file", "close"],
                },
                "chat": {
                    "type": "string",
                    "description": "Contact or group name exactly as it appears in WhatsApp "
                                   "(action=read/send/send_file).",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send (action=send). For send_file, optional caption.",
                },
                "path": {
                    "type": "string",
                    "description": "Absolute path of the file to send (action=send_file).",
                },
                "max_messages": {
                    "type": "integer",
                    "description": "How many recent messages to return (action=read, default 15).",
                },
            },
            "required": ["action"],
        },
    },
}

# ── resilient selectors (WhatsApp Web obfuscates classes; try several) ────────────────
_SEL_LOGGED_IN = ["#pane-side", "div[aria-label='Chat list']", "div[aria-label='Lista de chats']"]
_SEL_QR = ["canvas[aria-label*='scan' i]", "div[data-ref]", "div[aria-label*='QR' i]"]
_SEL_SEARCH = [
    "div[contenteditable='true'][data-tab='3']",
    "div[aria-label='Search input textbox']",
    "div[title='Search input textbox']",
    "div[role='textbox'][data-tab='3']",
    "div[contenteditable='true'][data-tab='4']",
]
_SEL_RESULT = ["#pane-side div[role='listitem']", "#pane-side div[role='row']"]
_SEL_MSGBOX = [
    "div[contenteditable='true'][data-tab='10']",
    "div[aria-label='Type a message']",
    "div[aria-label='Escribe un mensaje']",
    "footer div[contenteditable='true']",
]
_SEL_SEND = ["button[aria-label='Send']", "button[aria-label='Enviar']",
             "span[data-icon='send']", "span[data-icon='wds-ic-send-filled']"]
_SEL_ATTACH = ["button[aria-label='Attach']", "button[aria-label='Adjuntar']",
               "div[title='Attach'] button", "div[title='Adjuntar'] button",
               "span[data-icon='plus-rounded']", "span[data-icon='clip']",
               "span[data-icon='attach-menu-plus']"]
_SEL_MSG_IN = "div.message-in"
_SEL_MSG_OUT = "div.message-out"


# ── one persistent browser per process (the "open WhatsApp" cortex travels in) ─────────
_state: dict = {"pw": None, "ctx": None, "page": None}


def _confirm(title: str, lines: "list[str]", danger: bool = False) -> bool:
    try:
        from cortex.display import confirm_action
        return confirm_action(title, lines, danger=danger)
    except Exception:
        return False


def _first_visible(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            continue
    return None


def _any_present(page, selectors) -> bool:
    for sel in selectors:
        try:
            if page.query_selector(sel):
                return True
        except Exception:
            continue
    return False


def _get_page(headless: bool = False):
    """Return the live WhatsApp page, launching the persistent browser if needed."""
    if _state["page"] is not None:
        return _state["page"]
    from playwright.sync_api import sync_playwright

    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=str(_PROFILE_DIR),
        headless=headless,
        viewport=None,
        args=["--start-maximized"],
        locale="es-CL",
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        page.goto(_WA_URL, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass
    _state.update(pw=pw, ctx=ctx, page=page)
    return page


def _ensure_logged_in(page, wait_s: int = 90) -> "str | None":
    """Wait until the chat list is present. Returns an error/instruction string if not."""
    # Already in?
    if _any_present(page, _SEL_LOGGED_IN):
        return None
    # Is the QR showing? Tell the user to scan; poll for login.
    waited = 0
    while waited < wait_s:
        if _any_present(page, _SEL_LOGGED_IN):
            return None
        page.wait_for_timeout(1500)
        waited += 1.5
    return ("[ERROR] WhatsApp no está logueado. Se abrió la ventana — escanea el QR con tu "
            "teléfono (WhatsApp → Dispositivos vinculados). Queda guardado; reintenta la acción.")


def _open_chat(page, name: str) -> "str | None":
    """Open a chat by name via the search box. Returns error string or None on success."""
    box = _first_visible(page, _SEL_SEARCH)
    if not box:
        return "[ERROR] No encuentro el buscador de WhatsApp. ¿Está cargada la página?"
    try:
        box.click()
        page.wait_for_timeout(300)
        # Clear any previous query
        page.keyboard.press("Control+A")
        page.keyboard.press("Delete")
        page.keyboard.type(name, delay=40)
        page.wait_for_timeout(1500)
    except Exception as e:
        return f"[ERROR] No pude escribir en el buscador: {e}"

    result = _first_visible(page, _SEL_RESULT)
    if not result:
        return f"[ERROR] No hay resultados para '{name}'. Revisa el nombre exacto del contacto."
    try:
        result.click()
        page.wait_for_timeout(1200)
    except Exception as e:
        return f"[ERROR] No pude abrir el chat '{name}': {e}"
    return None


def _read_messages(page, n: int) -> str:
    rows = []
    try:
        ins = page.query_selector_all(_SEL_MSG_IN)
        outs = page.query_selector_all(_SEL_MSG_OUT)
    except Exception:
        ins, outs = [], []

    items = []
    for el in ins:
        items.append(("◀ ellos", el))
    for el in outs:
        items.append(("▶ tú", el))
    # WhatsApp renders messages in DOM order; re-sort by vertical position.
    def _top(el):
        try:
            bb = el.bounding_box()
            return bb["y"] if bb else 0
        except Exception:
            return 0
    items.sort(key=lambda t: _top(t[1]))

    for who, el in items[-max(1, n):]:
        try:
            spans = el.query_selector_all("span.selectable-text")
            txt = " ".join((s.inner_text() or "").strip() for s in spans).strip()
            if not txt:
                txt = "(adjunto / sin texto)"
            rows.append(f"{who}: {txt}")
        except Exception:
            continue
    return "\n".join(rows) if rows else "(no se leyeron mensajes — ¿chat vacío o selectores cambiados?)"


def _send_text(page, text: str) -> "str | None":
    box = _first_visible(page, _SEL_MSGBOX)
    if not box:
        return "[ERROR] No encuentro el cuadro de mensaje. ¿Está el chat abierto?"
    try:
        box.click()
        page.keyboard.type(text, delay=15)
        page.wait_for_timeout(300)
        btn = _first_visible(page, _SEL_SEND)
        if btn:
            btn.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_timeout(800)
    except Exception as e:
        return f"[ERROR] No pude enviar el mensaje: {e}"
    return None


def _send_file(page, path: str, caption: str) -> "str | None":
    attach = _first_visible(page, _SEL_ATTACH)
    if not attach:
        return "[ERROR] No encuentro el botón de adjuntar."
    try:
        attach.click()
        page.wait_for_timeout(600)
        finput = page.query_selector("input[type='file']")
        if not finput:
            # try again after menu animation
            page.wait_for_timeout(600)
            finput = page.query_selector("input[type='file']")
        if not finput:
            return "[ERROR] No apareció el selector de archivo."
        finput.set_input_files(path)
        page.wait_for_timeout(1500)
        if caption:
            cap = _first_visible(page, _SEL_MSGBOX) or _first_visible(
                page, ["div[contenteditable='true']"])
            if cap:
                cap.click()
                page.keyboard.type(caption, delay=15)
        page.wait_for_timeout(400)
        btn = _first_visible(page, _SEL_SEND)
        if btn:
            btn.click()
        else:
            page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
    except Exception as e:
        return f"[ERROR] No pude enviar el archivo: {e}"
    return None


def _list_chats(page, n: int = 15) -> str:
    names = []
    try:
        for row in page.query_selector_all("#pane-side div[role='listitem']")[:n]:
            try:
                t = (row.inner_text() or "").strip().splitlines()
                if t:
                    names.append(f"• {t[0]}")
            except Exception:
                continue
    except Exception:
        pass
    return "\n".join(names) if names else "(no se encontraron chats)"


def close() -> str:
    if _state["ctx"] is not None:
        try:
            _state["ctx"].close()
        except Exception:
            pass
    if _state["pw"] is not None:
        try:
            _state["pw"].stop()
        except Exception:
            pass
    _state.update(pw=None, ctx=None, page=None)
    return "✓ WhatsApp cerrado."


def execute(action: str, chat: str = "", text: str = "", path: str = "",
            max_messages: int = 15, **_) -> str:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        return ("[ERROR] Playwright no está instalado.\n"
                "Instala: pip install playwright && playwright install chromium")

    if action == "close":
        return close()

    try:
        page = _get_page()
    except Exception as e:
        return f"[ERROR] No pude abrir el navegador de WhatsApp: {type(e).__name__}: {e}"

    login_err = _ensure_logged_in(page)
    if login_err:
        return login_err

    if action == "status":
        return "✓ WhatsApp conectado y logueado. Listo para leer y enviar."

    if action == "list":
        return "Chats recientes:\n" + _list_chats(page, max_messages or 15)

    if action in ("read", "send", "send_file"):
        if not chat:
            return "[ERROR] Falta 'chat' (nombre del contacto o grupo)."
        err = _open_chat(page, chat)
        if err:
            return err

    if action == "read":
        msgs = _read_messages(page, int(max_messages or 15))
        return f"Últimos mensajes con {chat}:\n{msgs}"

    if action == "send":
        if not text:
            return "[ERROR] Falta 'text' (el mensaje a enviar)."
        preview = text[:300] + ("…" if len(text) > 300 else "")
        if not _confirm("✉ Enviar WhatsApp", [f"Para:  {chat}", "", preview]):
            return "[CANCELLED] Envío cancelado por el usuario."
        err = _send_text(page, text)
        return err or f"✓ Mensaje enviado a {chat}."

    if action == "send_file":
        if not path:
            return "[ERROR] Falta 'path' (archivo a enviar)."
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.exists():
            return f"[ERROR] El archivo no existe: {p}"
        lines = [f"Para:     {chat}", f"Archivo:  {p.name}"]
        if text:
            lines += ["", f"Caption: {text[:200]}"]
        if not _confirm("📎 Enviar archivo por WhatsApp", lines):
            return "[CANCELLED] Envío cancelado por el usuario."
        err = _send_file(page, str(p), text)
        return err or f"✓ Archivo '{p.name}' enviado a {chat}."

    return f"[ERROR] whatsapp: acción desconocida '{action}'."
