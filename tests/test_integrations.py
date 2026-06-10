"""Integration layer + gmail tool — no network, no real OAuth."""

from __future__ import annotations

import importlib

from cortex.config import Settings
from cortex.tools import gmail
from cortex.tools.registry import ToolRegistry


def test_gmail_registered_and_schema_valid():
    reg = ToolRegistry.default(Settings.load())
    assert "gmail" in reg
    fn = gmail.SCHEMA["function"]
    assert fn["name"] == "gmail"
    assert set(fn["parameters"]["properties"]) >= {"action", "query", "id"}
    assert fn["parameters"]["required"] == ["action"]


def test_gmail_execute_not_connected_returns_error(monkeypatch, tmp_path):
    """With no connected account, the tool must fail gracefully (no exception)."""
    from cortex.integrations import google_auth

    # Point the credential store at an empty temp dir → no accounts.
    monkeypatch.setattr(google_auth, "_GOOGLE_DIR", tmp_path / "google")
    monkeypatch.setattr(google_auth, "_ACTIVE_PTR", tmp_path / "google" / "active.txt")

    out = gmail.execute(action="search", query="is:unread", settings=Settings.load())
    assert out.startswith("[ERROR]")
    assert "gmail" in out.lower() or "google" in out.lower()


def test_account_store_roundtrip(monkeypatch, tmp_path):
    from cortex.integrations import google_auth

    gdir = tmp_path / "google"
    monkeypatch.setattr(google_auth, "_GOOGLE_DIR", gdir)
    monkeypatch.setattr(google_auth, "_ACTIVE_PTR", gdir / "active.txt")

    assert google_auth.list_accounts() == []
    assert google_auth.active_account() is None

    # Simulate two connected accounts by writing token files directly.
    gdir.mkdir(parents=True)
    (gdir / "a@gmail.com.json").write_text("{}", encoding="utf-8")
    (gdir / "b@gmail.com.json").write_text("{}", encoding="utf-8")

    assert google_auth.list_accounts() == ["a@gmail.com", "b@gmail.com"]
    # active defaults to first when no pointer set
    assert google_auth.active_account() == "a@gmail.com"

    google_auth.set_active("b@gmail.com")
    assert google_auth.active_account() == "b@gmail.com"

    assert google_auth.disconnect("b@gmail.com") is True
    assert "b@gmail.com" not in google_auth.list_accounts()


def test_project_id_of(tmp_path):
    from cortex.integrations import google_auth

    good = tmp_path / "client_secret.json"
    good.write_text('{"installed": {"client_id": "123-abc", "project_id": "prueba-487423"}}',
                    encoding="utf-8")
    assert google_auth.project_id_of(good) == "prueba-487423"

    web = tmp_path / "web.json"
    web.write_text('{"web": {"project_id": "myproj-1"}}', encoding="utf-8")
    assert google_auth.project_id_of(web) == "myproj-1"

    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert google_auth.project_id_of(bad) is None
    assert google_auth.project_id_of(None) is None


def test_modules_import_without_google_deps():
    """The tool + auth modules must import even if google-* isn't installed."""
    import cortex.integrations.google_auth as ga
    importlib.reload(ga)
    assert hasattr(ga, "connect") and hasattr(ga, "get_credentials")
