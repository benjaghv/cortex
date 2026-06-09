"""
Filesystem tool — read, write, list, search files locally.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

SCHEMA = {
    "type": "function",
    "function": {
        "name": "filesystem",
        "description": (
            "Read files, write files, create folders, list directories, or search for files. "
            "action='read' returns file contents, 'write' saves content, "
            "'mkdir' creates an empty folder (use this to make a directory), "
            "'list' shows a directory tree, 'search' finds files matching a glob pattern."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write", "mkdir", "list", "search"],
                },
                "path": {"type": "string", "description": "File or dir path. ~ expands to home."},
                "content": {"type": "string", "description": "Content to write (action='write')."},
                "pattern": {"type": "string", "description": "Glob, e.g. '*.py' (action='search')."},
                "max_depth": {"type": "integer", "description": "Depth for list/search. Default 3."},
            },
            "required": ["action", "path"],
        },
    },
}


def _resolve(path: str) -> Path:
    # Expand %VAR% / $VAR (Windows + POSIX) then ~ before resolving.
    return Path(os.path.expandvars(path)).expanduser().resolve()


def _not_found_hint(orig: str, resolved: Path) -> str:
    """Richer not-found error: show cwd + suggest matches for the basename."""
    cwd = Path.cwd()
    msg = f"[ERROR] File not found: {resolved}\n(cwd: {cwd})"
    name = Path(orig).name
    if name:
        hits = [str(m) for m in cwd.rglob(name)][:5]
        if hits:
            msg += "\nDid you mean one of these (use the absolute path)?\n" + "\n".join(hits)
    return msg


def execute(
    action: str,
    path: str,
    content: str | None = None,
    pattern: str | None = None,
    max_depth: int = 3,
) -> str:
    p = _resolve(path)

    if action == "read":
        if not p.exists():
            return _not_found_hint(path, p)
        if not p.is_file():
            return f"[ERROR] Not a file: {p}"
        size = p.stat().st_size
        if size > 500_000:
            return f"[GUARD] File too large ({size/1024:.0f}KB). Use shell head/Get-Content instead."
        return p.read_text(encoding="utf-8", errors="replace")

    if action == "write":
        if content is None:
            return "[ERROR] 'content' required for write."
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {p}"

    if action == "mkdir":
        if p.exists():
            return f"Folder already exists: {p}"
        p.mkdir(parents=True, exist_ok=True)
        return f"Created folder: {p}"

    if action == "list":
        if not p.exists():
            return f"[ERROR] Path not found: {p}"
        lines: list[str] = []

        def _walk(dirp: Path, depth: int, prefix: str = "") -> None:
            if depth > max_depth:
                return
            try:
                entries = sorted(dirp.iterdir(), key=lambda x: (x.is_file(), x.name))
            except PermissionError:
                return
            for e in entries:
                icon = "📄" if e.is_file() else "📁"
                lines.append(f"{prefix}{icon} {e.name}")
                if e.is_dir():
                    _walk(e, depth + 1, prefix + "  ")

        _walk(p, 1)
        return "\n".join(lines) if lines else "(empty)"

    if action == "search":
        pat = pattern or "*"
        matches: list[str] = []

        def _search(dirp: Path, depth: int) -> None:
            if depth > max_depth:
                return
            try:
                for e in dirp.iterdir():
                    if fnmatch.fnmatch(e.name, pat):
                        matches.append(str(e))
                    if e.is_dir():
                        _search(e, depth + 1)
            except PermissionError:
                pass

        _search(p, 1)
        if not matches:
            return f"No files matching '{pat}' under {p}"
        out = "\n".join(matches[:100])
        return out + (f"\n… ({len(matches)-100} more)" if len(matches) > 100 else "")

    return f"[ERROR] Unknown action: {action}"
