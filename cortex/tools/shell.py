"""
Shell tool — run commands locally.
Safety: allowlist of base commands + blocked substrings + timeout.
Cross-platform (uses the OS shell).
"""

from __future__ import annotations

import shlex
import subprocess

from cortex.config import Settings

SCHEMA = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": (
            "Execute a shell command on the local machine and return stdout/stderr. "
            "Use for git, listing files, running scripts, checking system state. "
            "Prefer specific commands over broad ones."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Full command to execute."},
                "cwd": {"type": "string", "description": "Working directory. Optional."},
            },
            "required": ["command"],
        },
    },
}


def execute(command: str, cwd: str | None = None, settings: Settings | None = None) -> str:
    cfg = settings or Settings.load()

    low = command.lower()
    for blocked in cfg.shell_blocked_patterns:
        if blocked.lower() in low:
            return f"[BLOCKED] Matches blocked pattern: '{blocked}'"

    try:
        base = (shlex.split(command, posix=False)[0] if command.strip() else "").strip('"')
    except ValueError as e:
        return f"[ERROR] Could not parse command: {e}"

    if cfg.shell_allowed_commands and base not in cfg.shell_allowed_commands:
        return (
            f"[BLOCKED] '{base}' not in allowed commands.\n"
            f"Allowed: {', '.join(cfg.shell_allowed_commands)}\n"
            f"Add it to shell_allowed_commands in ~/.cortex/config.toml."
        )

    try:
        r = subprocess.run(
            command, shell=True, capture_output=True,
            timeout=cfg.shell_timeout_seconds, cwd=cwd,
        )
        out = r.stdout.decode("utf-8", errors="replace").strip()
        err = r.stderr.decode("utf-8", errors="replace").strip()
        if r.returncode != 0:
            return f"[EXIT {r.returncode}]\n{err or out}"
        return out or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] Exceeded {cfg.shell_timeout_seconds}s."
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"
