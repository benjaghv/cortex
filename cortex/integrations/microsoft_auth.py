"""
cortex.integrations.microsoft_auth
────────────────────────────────────
Shared Microsoft (Azure AD / Entra) OAuth layer for Microsoft Graph. Service-agnostic
so Outlook, OneDrive and SharePoint tools all reuse it — only the scopes/endpoints change.

Design (mirrors google_auth):
  - One-time, CLI-friendly login via DEVICE CODE flow: `connect()` prints a short code,
    you open microsoft.com/devicelogin, paste it, approve → token cached locally.
  - No client secret, no local redirect server (public client + device flow).
  - Multiple accounts, switchable: an MSAL token cache per email; `active.txt` points
    at the one agents use.
  - `msal` is imported LAZILY so a missing dep fails with a clear message.

You bring your own Azure app registration (free):
  1. portal.azure.com → Microsoft Entra ID → App registrations → New registration
       - Supported account types: "Accounts in any org directory and personal" (multi-tenant)
  2. Authentication → Advanced → "Allow public client flows" = Yes
  3. API permissions → Microsoft Graph → Delegated → add: Mail.Read, Mail.Send,
       Mail.ReadWrite, offline_access, User.Read   (Sites.Read.All later for SharePoint)
  4. Copy the "Application (client) ID" → put it in ~/.cortex/config.toml as
       microsoft_client_id = "..."
  5. Run:  cortex connect outlook

Storage under ~/.cortex/credentials/microsoft/:
    <email>.json   ← serialized MSAL token cache (incl. refresh token) per account
    active.txt     ← email of the active account
"""

from __future__ import annotations

from pathlib import Path

from cortex.config import CREDENTIALS_DIR, Settings

GRAPH = "https://graph.microsoft.com/v1.0"

# Graph delegated scopes. offline_access → refresh token. Reserved OIDC scopes
# (openid/profile) are added by MSAL automatically.
DEFAULT_SCOPES = [
    "Mail.Read",
    "Mail.Send",
    "Mail.ReadWrite",
    "Sites.Read.All",        # SharePoint: read sites + files
    "Files.ReadWrite.All",   # SharePoint/OneDrive: read + write files
    "User.Read",
    "offline_access",
]

_MS_DIR = CREDENTIALS_DIR / "microsoft"
_ACTIVE_PTR = _MS_DIR / "active.txt"


class MicrosoftAuthError(RuntimeError):
    """Base for auth problems with a user-facing message."""


class MissingDepsError(MicrosoftAuthError):
    pass


class MissingClientIdError(MicrosoftAuthError):
    pass


class NotConnectedError(MicrosoftAuthError):
    pass


_SETUP_HINT = (
    "Falta el Application (client) ID de tu app de Azure.\n"
    "Configúralo UNA vez:\n"
    "  1. portal.azure.com → Microsoft Entra ID → App registrations → New registration\n"
    "     (Supported account types: cualquier organización + cuentas personales)\n"
    "  2. Authentication → 'Allow public client flows' = Yes\n"
    "  3. API permissions → Microsoft Graph → Delegated → Mail.Read, Mail.Send,\n"
    "     Mail.ReadWrite, Sites.Read.All, Files.ReadWrite.All, offline_access, User.Read\n"
    "  4. Copia el 'Application (client) ID' y ponlo en ~/.cortex/config.toml:\n"
    "       microsoft_client_id = \"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\"\n"
    "  5. Corre:  cortex connect outlook"
)


# ── paths / store ──────────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    _MS_DIR.mkdir(parents=True, exist_ok=True)


def _cache_path(email: str) -> Path:
    return _MS_DIR / f"{email}.json"


def list_accounts() -> list[str]:
    if not _MS_DIR.exists():
        return []
    return sorted(p.stem for p in _MS_DIR.glob("*.json"))


def active_account() -> "str | None":
    if _ACTIVE_PTR.exists():
        email = _ACTIVE_PTR.read_text(encoding="utf-8").strip()
        if email and _cache_path(email).exists():
            return email
    accounts = list_accounts()
    return accounts[0] if accounts else None


def set_active(email: str) -> None:
    if not _cache_path(email).exists():
        raise NotConnectedError(f"La cuenta '{email}' no está conectada.")
    _ensure_dir()
    _ACTIVE_PTR.write_text(email, encoding="utf-8")


def disconnect(email: str) -> bool:
    cp = _cache_path(email)
    existed = cp.exists()
    if existed:
        cp.unlink()
    if active_account() == email or not list_accounts():
        if _ACTIVE_PTR.exists():
            _ACTIVE_PTR.unlink()
    return existed


def _restrict_perms(path: Path) -> None:
    try:
        import os
        os.chmod(path, 0o600)
    except Exception:
        pass


# ── lazy msal import ────────────────────────────────────────────────────────────────────

def _msal():
    try:
        import msal
        return msal
    except ImportError as e:
        raise MissingDepsError(
            "Soporte de Microsoft no instalado. Reinstala con:\n"
            "  git pull && pip install -e \".[dev]\"\n"
            f"({e})"
        ) from e


def _client_id(cfg: Settings) -> str:
    cid = getattr(cfg, "microsoft_client_id", None)
    if not cid:
        raise MissingClientIdError(_SETUP_HINT)
    return cid


def _authority(cfg: Settings) -> str:
    tenant = getattr(cfg, "microsoft_tenant", None) or "common"
    return f"https://login.microsoftonline.com/{tenant}"


def _scopes(cfg: Settings) -> list[str]:
    # MSAL manages offline_access/openid itself; pass only the resource scopes.
    raw = getattr(cfg, "outlook_scopes", None) or DEFAULT_SCOPES
    return [s for s in raw if s not in ("offline_access", "openid", "profile")]


def _load_cache(email: "str | None"):
    msal = _msal()
    cache = msal.SerializableTokenCache()
    if email:
        cp = _cache_path(email)
        if cp.exists():
            cache.deserialize(cp.read_text(encoding="utf-8"))
    return cache


def _save_cache(email: str, cache) -> None:
    if cache.has_state_changed:
        _ensure_dir()
        cp = _cache_path(email)
        cp.write_text(cache.serialize(), encoding="utf-8")
        _restrict_perms(cp)


# ── public API ───────────────────────────────────────────────────────────────────────

def connect(cfg: Settings, on_message=None) -> str:
    """Device-code login. `on_message(text)` shows the user the code+URL. Returns email."""
    msal = _msal()
    cache = msal.SerializableTokenCache()
    app = msal.PublicClientApplication(
        _client_id(cfg), authority=_authority(cfg), token_cache=cache,
    )
    flow = app.initiate_device_flow(scopes=_scopes(cfg))
    if "user_code" not in flow:
        raise MicrosoftAuthError(
            f"No pude iniciar el device flow: {flow.get('error_description', flow)}"
        )
    msg = flow.get("message") or (
        f"Abre {flow.get('verification_uri')} e ingresa el código: {flow['user_code']}"
    )
    if on_message:
        on_message(msg)
    else:
        print(msg)

    result = app.acquire_token_by_device_flow(flow)  # blocks until you approve / timeout
    if "access_token" not in result:
        raise MicrosoftAuthError(
            f"Login falló: {result.get('error_description', result.get('error', 'desconocido'))}"
        )

    accounts = app.get_accounts()
    email = (accounts[0].get("username") if accounts else None) or "unknown"
    _save_cache(email, cache)
    set_active(email)
    return email


def get_token(cfg: Settings, account: "str | None" = None) -> str:
    """Return a valid Graph access token for the active/given account (silent refresh)."""
    msal = _msal()
    email = account or active_account()
    if not email:
        raise NotConnectedError("No hay cuenta de Microsoft conectada. Corre: cortex connect outlook")
    cache = _load_cache(email)
    app = msal.PublicClientApplication(
        _client_id(cfg), authority=_authority(cfg), token_cache=cache,
    )
    accounts = app.get_accounts(username=email) or app.get_accounts()
    if not accounts:
        raise NotConnectedError(f"La cuenta '{email}' no está conectada. Corre: cortex connect outlook")
    result = app.acquire_token_silent(_scopes(cfg), account=accounts[0])
    _save_cache(email, cache)
    if not result or "access_token" not in result:
        raise NotConnectedError(
            f"La sesión de '{email}' expiró. Reconéctate: cortex connect outlook"
        )
    return result["access_token"]


# ── Graph REST helpers ──────────────────────────────────────────────────────────────────

def _headers(cfg: Settings, account: "str | None", extra: "dict | None" = None) -> dict:
    h = {"Authorization": f"Bearer {get_token(cfg, account)}",
         "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def graph_get(cfg: Settings, path: str, params: "dict | None" = None,
              account: "str | None" = None, headers: "dict | None" = None) -> dict:
    import httpx
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    with httpx.Client(timeout=30) as c:
        r = c.get(url, params=params, headers=_headers(cfg, account, headers))
        r.raise_for_status()
        return r.json() if r.content else {}


def graph_post(cfg: Settings, path: str, json_body: "dict | None" = None,
               account: "str | None" = None, headers: "dict | None" = None) -> dict:
    import httpx
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    with httpx.Client(timeout=30) as c:
        r = c.post(url, json=json_body, headers=_headers(cfg, account, headers))
        r.raise_for_status()
        return r.json() if r.content else {}


def graph_get_bytes(cfg: Settings, path: str, account: "str | None" = None) -> bytes:
    """GET raw bytes (file content / download). Follows the redirect Graph returns."""
    import httpx
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    token = get_token(cfg, account)
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        r = c.get(url, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.content


def graph_put_bytes(cfg: Settings, path: str, data: bytes, account: "str | None" = None,
                    content_type: str = "application/octet-stream") -> dict:
    """PUT raw bytes (small-file upload, <4 MB)."""
    import httpx
    url = path if path.startswith("http") else f"{GRAPH}{path}"
    token = get_token(cfg, account)
    with httpx.Client(timeout=120) as c:
        r = c.put(url, content=data,
                  headers={"Authorization": f"Bearer {token}", "Content-Type": content_type})
        r.raise_for_status()
        return r.json() if r.content else {}
