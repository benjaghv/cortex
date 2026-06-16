"""
cortex.tools.sharepoint
─────────────────────────
Browse and manage SharePoint document libraries via Microsoft Graph. Reuses the
shared cortex.integrations.microsoft_auth (same Azure app + token as Outlook).

Actions:
  sites    — find SharePoint sites you can access (by name).
  list     — list folders/files inside a site's document library (a folder path).
  read     — return the text of a file (docx/txt/pdf-ish are returned as-is bytes→text).
  download — save a file from SharePoint to a local path.
  upload   — upload a local file to a SharePoint folder.
  search   — search files within a site.

Needs the Microsoft scopes Sites.Read.All + Files.ReadWrite.All. If the stored token
predates them, the tool returns a clear [ERROR] telling the user to reconnect.
"""

from __future__ import annotations

import os
from pathlib import Path

from cortex.config import Settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "sharepoint",
        "description": (
            "Browse and manage the user's SharePoint files via Microsoft 365. Use for ANY "
            "request about SharePoint sites, document libraries, or shared files.\n"
            "action='sites' (query): find sites by name.\n"
            "action='list' (site, path): list folders/files in a site's library (path optional, "
            "'' = root).\n"
            "action='read' (site, path): read a file's text.\n"
            "action='download' (site, path, dest): save a SharePoint file to a local path.\n"
            "action='upload' (site, path, source): upload a local file to a SharePoint folder.\n"
            "action='search' (site, query): search files in a site."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["sites", "list", "read", "download", "upload", "search"],
                },
                "site": {
                    "type": "string",
                    "description": "Site name (or part of it). cortex resolves it to the site id. "
                                   "Required for list/read/download/upload/search.",
                },
                "path": {
                    "type": "string",
                    "description": "File or folder path inside the document library, e.g. "
                                   "'Documentos compartidos/Informes/2026.xlsx'. '' = library root.",
                },
                "query": {
                    "type": "string",
                    "description": "Search text (action=sites or search).",
                },
                "dest": {
                    "type": "string",
                    "description": "Local destination path (action=download).",
                },
                "source": {
                    "type": "string",
                    "description": "Local source file path to upload (action=upload).",
                },
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


# ── site / drive resolution ────────────────────────────────────────────────────────────

def _find_site(cfg, query: str) -> "tuple[str, str] | str":
    """Return (site_id, displayName) for the best match, or an [ERROR] string."""
    from cortex.integrations import microsoft_auth as ms
    if not query:
        return "[ERROR] sharepoint: falta 'site' (nombre del sitio)."
    # A raw id (contains a comma) → use directly.
    if "," in query and "." in query:
        return (query, query)
    data = ms.graph_get(cfg, "/sites", params={"search": query})
    sites = data.get("value", [])
    if not sites:
        return f"[ERROR] No encontré ningún sitio de SharePoint para '{query}'."
    s = sites[0]
    return (s["id"], s.get("displayName") or s.get("name") or query)


def _item_path(path: str) -> str:
    """Build the Graph drive addressing suffix for a path ('' → root)."""
    p = (path or "").strip().strip("/")
    if not p:
        return "/drive/root"
    return f"/drive/root:/{p}:"


# ── actions ────────────────────────────────────────────────────────────────────────────

def _sites(cfg, query: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    data = ms.graph_get(cfg, "/sites", params={"search": query or "*"})
    sites = data.get("value", [])
    if not sites:
        return f"No encontré sitios para '{query or '(todos)'}'."
    lines = [f"{len(sites)} sitio(s):"]
    for s in sites[:20]:
        lines.append(f"• {s.get('displayName') or s.get('name')}  —  {s.get('webUrl', '')}")
    return "\n".join(lines)


def _list(cfg, site: str, path: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    resolved = _find_site(cfg, site)
    if isinstance(resolved, str):
        return resolved
    site_id, name = resolved
    suffix = _item_path(path)
    children_path = f"/sites/{site_id}{suffix}/children" if suffix.endswith(":") \
        else f"/sites/{site_id}{suffix}/children"
    data = ms.graph_get(cfg, children_path)
    items = data.get("value", [])
    if not items:
        return f"'{path or '(raíz)'}' en {name}: (vacío)"
    lines = [f"{name} → {path or '(raíz)'}  ({len(items)} elementos):"]
    for it in items:
        is_folder = "folder" in it
        icon = "📁" if is_folder else "📄"
        size = it.get("size", 0)
        sz = f"  ({size // 1024 + 1} KB)" if not is_folder else ""
        lines.append(f"  {icon} {it.get('name')}{sz}")
    return "\n".join(lines)


def _read(cfg, site: str, path: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not path:
        return "[ERROR] sharepoint: falta 'path' del archivo a leer."
    resolved = _find_site(cfg, site)
    if isinstance(resolved, str):
        return resolved
    site_id, name = resolved
    p = path.strip().strip("/")
    raw = ms.graph_get_bytes(cfg, f"/sites/{site_id}/drive/root:/{p}:/content")
    # Best-effort text extraction.
    text = _bytes_to_text(p, raw)
    return f"{name} → {path}:\n\n{text[:4000]}"


def _bytes_to_text(path: str, raw: bytes) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception:
            pass
    if ext == ".pdf":
        try:
            import io
            from pypdf import PdfReader
            r = PdfReader(io.BytesIO(raw))
            return "\n".join((pg.extract_text() or "") for pg in r.pages)
        except Exception:
            return "[No pude extraer el PDF — instala pypdf o descárgalo con action='download'.]"
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return "[Archivo binario — usa action='download' para guardarlo localmente.]"


def _download(cfg, site: str, path: str, dest: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not path or not dest:
        return "[ERROR] sharepoint: faltan 'path' (origen) y/o 'dest' (destino local)."
    resolved = _find_site(cfg, site)
    if isinstance(resolved, str):
        return resolved
    site_id, _ = resolved
    p = path.strip().strip("/")
    raw = ms.graph_get_bytes(cfg, f"/sites/{site_id}/drive/root:/{p}:/content")
    out = Path(os.path.expandvars(dest)).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    return f"✓ Descargado a {out}  ({len(raw) // 1024 + 1} KB)"


def _upload(cfg, site: str, path: str, source: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not path or not source:
        return "[ERROR] sharepoint: faltan 'path' (destino) y/o 'source' (archivo local)."
    src = Path(os.path.expandvars(source)).expanduser()
    if not src.exists():
        return f"[ERROR] El archivo local no existe: {src}"
    resolved = _find_site(cfg, site)
    if isinstance(resolved, str):
        return resolved
    site_id, name = resolved
    if not _confirm("📤 Subir a SharePoint",
                    [f"Sitio:   {name}", f"Destino: {path}", f"Archivo: {src.name} "
                     f"({src.stat().st_size // 1024 + 1} KB)"]):
        return "[CANCELLED] Subida cancelada por el usuario."
    p = path.strip().strip("/")
    data = src.read_bytes()
    item = ms.graph_put_bytes(cfg, f"/sites/{site_id}/drive/root:/{p}:/content", data)
    return f"✓ Subido a {name} → {item.get('name', p)}."


def _search(cfg, site: str, query: str) -> str:
    from cortex.integrations import microsoft_auth as ms
    if not query:
        return "[ERROR] sharepoint: falta 'query' para buscar."
    resolved = _find_site(cfg, site)
    if isinstance(resolved, str):
        return resolved
    site_id, name = resolved
    data = ms.graph_get(cfg, f"/sites/{site_id}/drive/root/search(q='{query}')")
    items = data.get("value", [])
    if not items:
        return f"Sin resultados para '{query}' en {name}."
    lines = [f"{name} — resultados de '{query}':"]
    for it in items[:20]:
        icon = "📁" if "folder" in it else "📄"
        lines.append(f"  {icon} {it.get('name')}  —  {it.get('webUrl', '')}")
    return "\n".join(lines)


# ── entrypoint ─────────────────────────────────────────────────────────────────────────

def execute(action: str, site: str = "", path: str = "", query: str = "",
            dest: str = "", source: str = "", settings: "Settings | None" = None, **_) -> str:
    from cortex.integrations import microsoft_auth as ms
    cfg = settings or Settings.load()
    try:
        if action == "sites":
            return _sites(cfg, query)
        if action == "list":
            return _list(cfg, site, path)
        if action == "read":
            return _read(cfg, site, path)
        if action == "download":
            return _download(cfg, site, path, dest)
        if action == "upload":
            return _upload(cfg, site, path, source)
        if action == "search":
            return _search(cfg, site, query)
        return (f"[ERROR] sharepoint: acción desconocida '{action}'. "
                "Usa sites, list, read, download, upload o search.")
    except ms.MicrosoftAuthError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "AccessDenied" in msg:
            return ("[ERROR] sharepoint: sin permiso o token viejo. Tu token puede no incluir "
                    "Sites.Read.All / Files.ReadWrite.All. Agrega esos permisos en Azure y "
                    "reconéctate: cortex connect outlook")
        return f"[ERROR] sharepoint {action}: {type(e).__name__}: {e}"
