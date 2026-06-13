"""
cortex.tools.registry
──────────────────────
Central registry mapping tool name → (OpenAI schema, executor callable).

Single source of truth for "what tools exist". Replaces the old inline
``_build_tool_registry`` in agent.py. Agents request a subset by name, so each
role only sees its own tools.

Adding a tool: import its module and add one ToolEntry in ``_default_entries``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from cortex.config import Settings
from cortex.tools import (
    browser,
    datetime_tool,
    document,
    filesystem,
    git_tool,
    gmail,
    outlook,
    pdf,
    pptx,
    python_exec,
    search,
    shell,
    stock,
    weather,
    web,
)
# whatsapp tool kept in cortex/tools/whatsapp.py but unregistered for now
# (WhatsApp Web automation was flaky / ban-risk). Re-add the import + entry to revive.

Executor = Callable[[dict], str]


@dataclass(frozen=True)
class ToolEntry:
    name: str
    schema: dict
    executor: Executor


def _default_entries(cfg: Settings) -> dict[str, ToolEntry]:
    """Build the full set of built-in tools. One place to register a new tool."""
    raw: list[tuple[str, dict, Executor]] = [
        ("filesystem", filesystem.SCHEMA, lambda a: filesystem.execute(**a)),
        ("shell",      shell.SCHEMA,      lambda a: shell.execute(settings=cfg, **a)),
        ("git",        git_tool.SCHEMA,   lambda a: git_tool.execute(**a)),
        ("web", web.SCHEMA, lambda a: web.execute(**a)),
        ("browser", browser.SCHEMA, lambda a: browser.execute(**a)),
        ("search", search.SCHEMA, lambda a: search.execute(**a)),
        ("stock", stock.SCHEMA, lambda a: stock.execute(**a)),
        ("datetime", datetime_tool.SCHEMA, lambda a: datetime_tool.execute()),
        ("weather", weather.SCHEMA, lambda a: weather.execute(**a)),
        ("python_exec", python_exec.SCHEMA, lambda a: python_exec.execute(**a)),
        ("document",   document.SCHEMA,    lambda a: document.execute(**a)),
        ("pdf",        pdf.SCHEMA,         lambda a: pdf.execute(**a)),
        ("pptx",       pptx.SCHEMA,        lambda a: pptx.execute(**a)),
        ("gmail",      gmail.SCHEMA,       lambda a: gmail.execute(settings=cfg, **a)),
        ("outlook",    outlook.SCHEMA,     lambda a: outlook.execute(settings=cfg, **a)),
    ]
    return {name: ToolEntry(name, schema, ex) for name, schema, ex in raw}


class ToolRegistry:
    """Holds tool entries and hands out schemas/executors, whole or by subset."""

    def __init__(self, entries: dict[str, ToolEntry]) -> None:
        self._entries = entries

    @classmethod
    def default(cls, cfg: Settings) -> "ToolRegistry":
        return cls(_default_entries(cfg))

    def subset(self, names: "list[str] | tuple[str, ...]") -> "ToolRegistry":
        """New registry with only the named tools (unknown names ignored)."""
        picked = {n: self._entries[n] for n in names if n in self._entries}
        return ToolRegistry(picked)

    def names(self) -> list[str]:
        return list(self._entries.keys())

    def schemas(self) -> list[dict]:
        return [e.schema for e in self._entries.values()]

    def executor(self, name: str) -> "Executor | None":
        entry = self._entries.get(name)
        return entry.executor if entry else None

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)
