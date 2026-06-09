"""
cortex.agents.prompt_base
─────────────────────────
Shared system-prompt scaffolding: OS hint, real machine paths, and the common
rules every agent obeys. Presets layer a role-specific focus on top of this.

Moved here (from agent.py) so both the single-agent path and the per-preset
agents build prompts from one source — no duplication, no import cycles.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

_OS = platform.system()  # 'Windows' | 'Linux' | 'Darwin'
_HOME = Path.home()


def _find_desktop() -> Path:
    """Real Desktop path. Handles Windows OneDrive redirect via registry."""
    if _OS == "Windows":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
            raw, _ = winreg.QueryValueEx(key, "Desktop")
            winreg.CloseKey(key)
            p = Path(os.path.expandvars(raw))
            if p.exists():
                return p
        except OSError:
            pass
        # Fallbacks: OneDrive redirect, then plain Home/Desktop.
        for cand in (_HOME / "OneDrive" / "Desktop", _HOME / "Desktop"):
            if cand.exists():
                return cand
    return _HOME / "Desktop"


_DESKTOP = _find_desktop()
_CWD = Path.cwd()

PATHS_HINT = (
    f"Real machine paths (use these absolute paths, never literal %VARS% or guesses):\n"
    f"  - Home: {_HOME}\n"
    f"  - Desktop: {_DESKTOP}\n"
    f"  - Current working dir: {_CWD}\n"
)

SHELL_HINT = (
    "The shell is Windows (cmd/PowerShell). Use Windows commands: 'dir' not 'ls', "
    "'type' not 'cat'. Do NOT use grep/sed/awk/head — they don't exist here."
    if _OS == "Windows"
    else "The shell is a POSIX shell (bash). Standard unix commands are available."
)

# Rules that apply to every agent, regardless of role.
COMMON_RULES = (
    "RULES:\n"
    "- Greetings, general questions, explanations → answer DIRECTLY, no tools.\n"
    "- Create a FOLDER → filesystem(action='mkdir', path=...). NEVER use 'write' for a folder "
    "(write needs 'content' and makes a file, not a directory).\n"
    "- 'on my desktop'/'en mi escritorio' → use the exact Desktop path listed above, not a guess.\n"
    "- Never invent or fabricate data. If a tool fails, say so honestly.\n"
    "- Read a local file with filesystem(action='read'), never shell.\n"
    "- Never repeat a tool call that already succeeded or failed.\n"
    "- Answer in readable markdown. Never raw JSON.\n"
    "- If tool output is long or truncated, use what you have — summarize it, list what you saw. "
    "NEVER say 'I can't assist', 'I'm sorry', or refuse because data was cut off. Work with partial data.\n"
    "- LANGUAGE: detect the language of the user's message and respond in that same language. "
    "If the user writes in Spanish → answer in Spanish. English → English. Always match the user."
)


def build_system_prompt(role_intro: str, tool_lines: str, role_rules: str = "") -> str:
    """Assemble a full system prompt: identity + OS/paths + tools + rules.

    - role_intro: who this agent is ("You are cortex's research specialist…").
    - tool_lines: the "  - name: desc" lines for this agent's tools.
    - role_rules: extra role-specific rules appended after the common ones.
    """
    extra = ("\n" + role_rules) if role_rules else ""
    return (
        f"{role_intro}\n"
        f"Operating system: {_OS}. {SHELL_HINT}\n"
        f"{PATHS_HINT}"
        "TOOLS AVAILABLE:\n"
        f"{tool_lines}\n"
        f"{COMMON_RULES}{extra}"
    )
