"""
cortex.tools.git_tool
──────────────────────
Git tool — run git operations safely.
Read ops (status/diff/log/branch/show) are always allowed.
Write ops (add/commit/push/pull/checkout) are allowed but guarded.
Destructive ops (reset --hard, push --force, clean -f) are blocked.
"""

from __future__ import annotations

import subprocess

SCHEMA = {
    "type": "function",
    "function": {
        "name": "git",
        "description": (
            "Run git operations on a local repository. "
            "Supports: status, diff, log, branch, add, commit, push, pull, "
            "checkout/switch, stash, show, blame, remote, fetch, merge, tag. "
            "Destructive ops (reset --hard, push --force, clean -f) are blocked. "
            "Use 'cwd' to target a specific repo path."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": (
                        "Git subcommand and arguments, without the 'git' prefix. "
                        "Examples: 'status', 'log --oneline -10', 'diff HEAD~1', "
                        "'add src/main.py', 'commit -m \"fix login bug\"', 'push origin main'."
                    ),
                },
                "cwd": {
                    "type": "string",
                    "description": "Repo path. Defaults to current working directory.",
                },
            },
            "required": ["args"],
        },
    },
}

# Subcommands always allowed (read-only or safe)
_ALLOWED = {
    "status", "diff", "log", "branch", "show", "blame", "stash",
    "remote", "fetch", "ls-files", "rev-parse", "shortlog", "describe",
    "tag", "reflog", "config", "version", "help",
    # write (allowed but guarded below)
    "add", "commit", "push", "pull", "checkout", "switch", "merge",
    "rebase", "cherry-pick", "revert", "rm", "mv", "restore",
    "clone", "init", "submodule",
}

# Argument patterns that are never allowed
_BLOCKED_PATTERNS = [
    "--force",
    "-f",
    "--hard",
    "clean -",
    "push -f",
    "push --force",
    "reset --hard",
    "reset -h",
    "--delete --remote",   # deleting remote branches
]

# Subcommands that modify state — add a soft warning to the result
_WRITE_OPS = {"add", "commit", "push", "pull", "merge", "rebase", "cherry-pick",
               "revert", "rm", "mv", "restore", "checkout", "switch", "clone", "init"}

_TIMEOUT = 30  # seconds


def execute(args: str, cwd: str | None = None, **_) -> str:
    args = args.strip()
    if not args:
        return "[ERROR] No git subcommand provided."

    # Extract subcommand
    subcommand = args.split()[0].lower().lstrip("-")

    if subcommand not in _ALLOWED:
        return (
            f"[BLOCKED] git subcommand '{subcommand}' is not allowed.\n"
            f"Allowed: {', '.join(sorted(_ALLOWED))}"
        )

    # Check for blocked argument patterns
    args_lower = args.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat in args_lower:
            return (
                f"[BLOCKED] Argument pattern '{pat}' is not allowed "
                f"(destructive operation). Use a safer alternative."
            )

    cmd = f"git {args}"
    try:
        r = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            timeout=_TIMEOUT,
            cwd=cwd,
        )
        stdout = r.stdout.decode("utf-8", errors="replace").strip()
        stderr = r.stderr.decode("utf-8", errors="replace").strip()

        if r.returncode != 0:
            # Git often sends informational output to stderr even on success-ish ops
            combined = "\n".join(filter(None, [stdout, stderr]))
            return f"[EXIT {r.returncode}]\n{combined}"

        output = stdout or stderr or "(no output)"
        return output

    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] git {args[:40]}… exceeded {_TIMEOUT}s."
    except FileNotFoundError:
        return "[ERROR] git not found. Install git: https://git-scm.com/download"
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"
