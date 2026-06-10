"""
cortex.integrations.google_auth
────────────────────────────────
Shared Google OAuth layer (BYO Google Cloud project). Service-agnostic so future
Google tools (Calendar, Drive) reuse it — only the scopes change.

Design:
  - One-time, intuitive login: `connect()` opens the browser, you approve, the
    refresh-token is stored locally → persistent.
  - Multiple accounts, switchable: tokens live per-email; an `active.txt` pointer
    selects which one agents use. Switching = rewriting that pointer.
  - All `google-*` imports are LAZY (inside functions) so cortex works without the
    optional deps. Install with:  pip install ".[google]"

Storage under ~/.cortex/credentials/:
    google_client_secret.json   ← your OAuth client (copied on first connect)
    google/<email>.json         ← authorized token (incl. refresh_token) per account
    google/active.txt           ← email of the active account
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex.config import CREDENTIALS_DIR, Settings

DEFAULT_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_GOOGLE_DIR = CREDENTIALS_DIR / "google"
_CLIENT_SECRET = CREDENTIALS_DIR / "google_client_secret.json"
_ACTIVE_PTR = _GOOGLE_DIR / "active.txt"


class GoogleAuthError(RuntimeError):
    """Base for auth problems with a user-facing message."""


class MissingDepsError(GoogleAuthError):
    pass


class MissingClientSecretError(GoogleAuthError):
    pass


class NotConnectedError(GoogleAuthError):
    pass


_SETUP_HINT = (
    "Falta el client_secret.json de tu proyecto de Google Cloud.\n"
    "Configúralo UNA vez:\n"
    "  1. console.cloud.google.com → crea un proyecto\n"
    "  2. Habilita la 'Gmail API'\n"
    "  3. OAuth consent screen → External → agrégate como test user\n"
    "     (o pon el proyecto 'In production' para tokens persistentes)\n"
    "  4. Credentials → Create OAuth client ID → 'Desktop app' → descarga el JSON\n"
    f"  5. Copia el JSON a:  {CREDENTIALS_DIR}\n"
    "  6. Corre:  cortex connect gmail   (cortex lo detecta solo)"
)


# ── paths / store ──────────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    _GOOGLE_DIR.mkdir(parents=True, exist_ok=True)


def _token_path(email: str) -> Path:
    return _GOOGLE_DIR / f"{email}.json"


def client_secret_path(cfg: Settings) -> "Path | None":
    """Where the OAuth client lives: configured path, else the credentials dir copy."""
    if cfg.google_client_secret_path:
        p = Path(cfg.google_client_secret_path).expanduser()
        if p.exists():
            return p
    return _CLIENT_SECRET if _CLIENT_SECRET.exists() else None


def _autodiscover_client_secret() -> "Path | None":
    """Find the Google OAuth client JSON so the user needn't pass a path.

    Preferred: any *.json the user dropped into ~/.cortex/credentials/.
    Fallback: a fresh `client_secret*.json` download in Downloads/cwd.
    Picks the newest valid installed/desktop OAuth client. None if nothing found.
    """
    candidates: list[Path] = []
    # 1) The canonical, stable location inside cortex.
    if CREDENTIALS_DIR.exists():
        candidates += list(CREDENTIALS_DIR.glob("*.json"))
    # 2) Convenience: a freshly downloaded file.
    for d in (Path.home() / "Downloads", Path.home() / "Descargas", Path.cwd()):
        if d.exists():
            candidates += list(d.glob("client_secret*.json"))

    valid: list[Path] = []
    for c in set(candidates):
        try:
            data = json.loads(c.read_text(encoding="utf-8"))
            if "installed" in data or "web" in data:  # an OAuth client, not a token
                valid.append(c)
        except Exception:
            continue
    if not valid:
        return None
    return max(valid, key=lambda p: p.stat().st_mtime)


def project_id_of(path: "str | Path | None") -> "str | None":
    """Read the GCP project_id from a client_secret JSON (installed/web)."""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        node = data.get("installed") or data.get("web") or {}
        return node.get("project_id") or None
    except Exception:
        return None


def list_accounts() -> list[str]:
    if not _GOOGLE_DIR.exists():
        return []
    return sorted(p.stem for p in _GOOGLE_DIR.glob("*.json"))


def active_account() -> "str | None":
    if _ACTIVE_PTR.exists():
        email = _ACTIVE_PTR.read_text(encoding="utf-8").strip()
        if email and _token_path(email).exists():
            return email
    accounts = list_accounts()
    return accounts[0] if accounts else None


def set_active(email: str) -> None:
    if not _token_path(email).exists():
        raise NotConnectedError(f"La cuenta '{email}' no está conectada.")
    _ensure_dir()
    _ACTIVE_PTR.write_text(email, encoding="utf-8")


def disconnect(email: str) -> bool:
    tp = _token_path(email)
    existed = tp.exists()
    if existed:
        tp.unlink()
    if active_account() == email or not list_accounts():
        if _ACTIVE_PTR.exists():
            _ACTIVE_PTR.unlink()
    return existed


def _restrict_perms(path: Path) -> None:
    """Best-effort: make a token file user-only-readable (no-op on most Windows)."""
    try:
        import os
        os.chmod(path, 0o600)
    except Exception:
        pass


# ── lazy google imports ──────────────────────────────────────────────────────────────

def _imports():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        return InstalledAppFlow, Credentials, Request, build
    except ImportError as e:
        raise MissingDepsError(
            "Soporte de Google no instalado. Instala con:  pip install \".[google]\"\n"
            f"({e})"
        ) from e


# ── public API ───────────────────────────────────────────────────────────────────────

def connect(cfg: Settings, client_secret_src: "str | None" = None) -> str:
    """Run the OAuth desktop flow, store the token, set it active. Returns the email."""
    InstalledAppFlow, _Credentials, _Request, build = _imports()
    _ensure_dir()

    # If no secret was given and none is stored yet, try to find a downloaded one
    # so the user can just run `cortex connect gmail` after downloading the JSON.
    if not client_secret_src and not client_secret_path(cfg):
        found = _autodiscover_client_secret()
        if found:
            client_secret_src = str(found)
            try:
                from cortex.display import console
                console.print(f"  [dim]Usando client secret detectado:[/] {found}")
            except Exception:
                pass

    # Copy a provided client_secret.json into the credentials dir (first-time setup).
    if client_secret_src:
        src = Path(client_secret_src).expanduser()
        if not src.exists():
            raise MissingClientSecretError(f"No existe el archivo: {src}")
        _CLIENT_SECRET.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        _restrict_perms(_CLIENT_SECRET)

    secret = client_secret_path(cfg)
    if not secret:
        raise MissingClientSecretError(_SETUP_HINT)

    scopes = cfg.gmail_scopes or DEFAULT_SCOPES
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), scopes=scopes)
    creds = flow.run_local_server(port=0)  # opens browser, captures code on localhost

    # Identify the account via the Gmail profile.
    service = build("gmail", "v1", credentials=creds)
    email = service.users().getProfile(userId="me").execute().get("emailAddress", "")
    if not email:
        raise GoogleAuthError("No se pudo obtener el email de la cuenta.")

    tp = _token_path(email)
    tp.write_text(creds.to_json(), encoding="utf-8")
    _restrict_perms(tp)
    set_active(email)
    return email


def get_credentials(cfg: Settings, account: "str | None" = None):
    """Load (and refresh if needed) the credentials for the active/given account."""
    _InstalledAppFlow, Credentials, Request, _build = _imports()

    email = account or active_account()
    if not email:
        raise NotConnectedError(
            "No hay cuenta de Google conectada. Corre:  cortex connect gmail"
        )
    tp = _token_path(email)
    if not tp.exists():
        raise NotConnectedError(f"La cuenta '{email}' no está conectada.")

    scopes = cfg.gmail_scopes or DEFAULT_SCOPES
    creds = Credentials.from_authorized_user_info(json.loads(tp.read_text(encoding="utf-8")), scopes)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tp.write_text(creds.to_json(), encoding="utf-8")
            _restrict_perms(tp)
        else:
            raise NotConnectedError(
                f"La sesión de '{email}' expiró. Reconéctate:  cortex connect gmail"
            )
    return creds


def gmail_service(cfg: Settings, account: "str | None" = None):
    """Authenticated Gmail API client for the active/given account."""
    _i, _c, _r, build = _imports()
    creds = get_credentials(cfg, account)
    return build("gmail", "v1", credentials=creds)
