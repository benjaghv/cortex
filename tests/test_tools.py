"""Tool unit tests — no LLM, no network."""

from __future__ import annotations

from pathlib import Path

from cortex.config import Settings
from cortex.tools import filesystem, shell


def test_fs_write_read(tmp_path: Path):
    f = tmp_path / "a.txt"
    out = filesystem.execute(action="write", path=str(f), content="hello")
    assert "Wrote" in out
    assert filesystem.execute(action="read", path=str(f)) == "hello"


def test_fs_read_missing(tmp_path: Path):
    out = filesystem.execute(action="read", path=str(tmp_path / "nope.txt"))
    assert out.startswith("[ERROR]")


def test_fs_list(tmp_path: Path):
    (tmp_path / "x.py").write_text("x")
    out = filesystem.execute(action="list", path=str(tmp_path))
    assert "x.py" in out


def test_fs_search(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.txt").write_text("x")
    out = filesystem.execute(action="search", path=str(tmp_path), pattern="*.py")
    assert "a.py" in out and "b.txt" not in out


def test_shell_blocked():
    cfg = Settings()
    out = shell.execute(command="shutdown now", settings=cfg)
    assert out.startswith("[BLOCKED]")


def test_shell_not_allowed():
    cfg = Settings(shell_allowed_commands=["git"])
    out = shell.execute(command="whoami", settings=cfg)
    assert out.startswith("[BLOCKED]")


def test_shell_echo():
    cfg = Settings()
    out = shell.execute(command="echo hi", settings=cfg)
    assert "hi" in out
