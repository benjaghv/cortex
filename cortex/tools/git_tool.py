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
                        "Chain multiple commands with ' && ' to run a full workflow in ONE call. "
                        "Examples: 'status', 'log --oneline -10', 'diff HEAD~1', "
                        "'add .', 'commit -m \"fix login bug\"', 'push', "
                        "'add . && commit -m \"feat: add feature\" && push'."
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
    "--delete --remote",
]

# After these ops, auto-append a status check so the agent sees the result
_VERIFY_OPS  = {"commit", "push", "pull", "merge", "rebase", "revert"}
# Push and pull: retry up to this many times on transient failure
_MAX_RETRIES = 2
_RETRY_OPS   = {"push", "pull"}
_TIMEOUT     = 30  # seconds per attempt

# Exit codes / stderr patterns that are worth retrying (transient / network)
_RETRY_SIGNALS = ("timeout", "timed out", "connection", "network", "unable to connect",
                  "could not resolve", "ssl", "temporarily unavailable")


def _run_git(args: str, cwd: str | None) -> tuple[int, str]:
    """Run `git <args>`, return (returncode, combined output)."""
    r = subprocess.run(
        f"git {args}",
        shell=True, capture_output=True, timeout=_TIMEOUT, cwd=cwd,
    )
    stdout = r.stdout.decode("utf-8", errors="replace").strip()
    stderr = r.stderr.decode("utf-8", errors="replace").strip()
    combined = "\n".join(filter(None, [stdout, stderr]))
    return r.returncode, combined or "(no output)"


def execute(args: str, cwd: str | None = None, **_) -> str:
    import time

    args = args.strip()
    if not args:
        return "[ERROR] No git subcommand provided."

    # Support chained commands: "add . && commit -m 'msg' && push"
    if " && " in args:
        parts_chain = [p.strip() for p in args.split(" && ") if p.strip()]
        results = []
        for cmd in parts_chain:
            res = execute(cmd, cwd=cwd)
            results.append(f"$ git {cmd}\n{res}")
            if res.startswith(("[ERROR]", "[BLOCKED]", "[EXIT", "[TIMEOUT]")):
                results.append("(chain stopped on error)")
                break
        return "\n\n".join(results)

    subcommand = args.split()[0].lower().lstrip("-")

    if subcommand not in _ALLOWED:
        return (
            f"[BLOCKED] git subcommand '{subcommand}' is not allowed.\n"
            f"Allowed: {', '.join(sorted(_ALLOWED))}"
        )

    args_lower = args.lower()
    for pat in _BLOCKED_PATTERNS:
        if pat in args_lower:
            return (
                f"[BLOCKED] Argument pattern '{pat}' is not allowed "
                "(destructive operation). Use a safer alternative."
            )

    try:
        # ── Execute with optional retry ───────────────────────────────────
        attempts = _MAX_RETRIES if subcommand in _RETRY_OPS else 1
        last_code, last_out = 0, ""

        for attempt in range(1, attempts + 1):
            last_code, last_out = _run_git(args, cwd)

            if last_code == 0:
                break  # success

            # Only retry on transient/network errors
            is_transient = any(sig in last_out.lower() for sig in _RETRY_SIGNALS)
            if attempt < attempts and is_transient:
                time.sleep(2 * attempt)  # 2s, 4s back-off
                continue
            break  # non-transient or exhausted retries

        # ── Build result ──────────────────────────────────────────────────
        parts: list[str] = []

        if last_code != 0:
            retry_note = f" (failed after {attempts} attempt{'s' if attempts > 1 else ''})" \
                         if attempts > 1 else ""
            parts.append(f"[EXIT {last_code}]{retry_note}\n{last_out}")
        else:
            parts.append(last_out)

        # ── Auto-verify after write ops ───────────────────────────────────
        if subcommand in _VERIFY_OPS:
            _, status_out = _run_git("status", cwd)
            parts.append(f"\n── post-{subcommand} status ──\n{status_out}")

            # After push, also confirm remote is in sync
            if subcommand == "push" and last_code == 0:
                _, log_out = _run_git("log --oneline -1", cwd)
                parts.append(f"\n── latest commit ──\n{log_out}")

        return "\n".join(parts)

    except subprocess.TimeoutExpired:
        return f"[TIMEOUT] git {args[:40]}… exceeded {_TIMEOUT}s."
    except FileNotFoundError:
        return "[ERROR] git not found. Install git: https://git-scm.com/download"
    except Exception as e:
        return f"[ERROR] {type(e).__name__}: {e}"
