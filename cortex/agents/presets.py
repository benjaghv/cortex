"""
cortex.agents.presets
──────────────────────
Built-in agent presets + loader. Each preset focuses a role on a tool subset.

Add a preset: append an AgentPreset to _BUILTINS (and optionally override via
~/.cortex/config.toml [agents.<name>] in a future iteration).
"""

from __future__ import annotations

from cortex.agents.preset import AgentPreset
from cortex.agents.prompt_base import build_system_prompt

# Tool descriptions, reused to build each preset's TOOLS block.
_TOOL_DESC = {
    "filesystem": "filesystem: read/write files, create folders (mkdir), list/search local files",
    "shell": "shell: local commands (git, dir) — Windows only, no bash/grep/curl",
    "python_exec": "python_exec: run Python → math, calculations, data processing (don't guess numbers)",
    "search": "search: DuckDuckGo web search → news, docs, general internet info",
    "web": "web: fetch a specific URL → when you have an exact URL",
    "stock": "stock: real-time stock/crypto price by ticker → ANY stock/share price question",
    "weather": "weather: current weather + forecast for a city → ANY weather question",
    "datetime": "datetime: current date/time → date, time, day of week, time-relative questions",
}

_ALL_TOOLS = tuple(_TOOL_DESC.keys())


def _tool_lines(tools: "tuple[str, ...]") -> str:
    return "\n".join(f"  - {_TOOL_DESC[t]}" for t in tools if t in _TOOL_DESC)


def _make(name: str, description: str, role_intro: str,
          tools: "tuple[str, ...]", role_rules: str = "") -> AgentPreset:
    return AgentPreset(
        name=name,
        description=description,
        system_prompt=build_system_prompt(role_intro, _tool_lines(tools), role_rules),
        tools=tools,
    )


_BUILTINS: dict[str, AgentPreset] = {}


def _register(preset: AgentPreset) -> None:
    _BUILTINS[preset.name] = preset


_register(_make(
    name="coder",
    description="Reads/writes code and files, runs shell commands and Python scripts.",
    role_intro="You are cortex's coding specialist. You work with local files, the shell, and Python.",
    tools=("filesystem", "shell", "python_exec"),
    role_rules=(
        "- Stay on the coding/files/scripts task you were given; do not browse the web.\n"
        "- Prefer filesystem over shell for reading/writing files."
    ),
))

_register(AgentPreset(
    name="researcher",
    description="Searches the web, fetches URLs, reads docs, and summarizes findings.",
    system_prompt=build_system_prompt(
        "You are cortex's research specialist. You find and synthesize information from the web and local docs.",
        _tool_lines(("search", "web", "filesystem")),
        "- Use 'search' for general queries, 'web' when you already have a URL.\n"
        "- Cite the source (site/URL) when you state a fact from the web.",
    ),
    tools=("search", "web", "filesystem"),
    model="ollama/deepseek-r1:8b",   # stronger reasoning for research/analysis
))

_register(_make(
    name="data",
    description="Real-time data: stock/crypto prices, weather, date/time, plus calculations.",
    role_intro="You are cortex's data specialist. You fetch live numbers and compute over them.",
    tools=("stock", "weather", "datetime", "python_exec"),
    role_rules=(
        "- Stock prices → 'stock' (Nvidia=NVDA, Apple=AAPL, Tesla=TSLA). Weather → 'weather'.\n"
        "- Use 'python_exec' for any arithmetic; never guess numbers."
    ),
))

_register(_make(
    name="generalist",
    description="Handles any task; has access to every tool. Default for simple requests.",
    role_intro="You are cortex, a helpful AI assistant with access to all local tools.",
    tools=_ALL_TOOLS,
    role_rules=(
        "- Stock prices → 'stock'. Weather → 'weather'. Date/time → 'datetime'. Math → 'python_exec'.\n"
        "- News, current events, docs → 'search'."
    ),
))


def all_presets() -> dict[str, AgentPreset]:
    return dict(_BUILTINS)


def get_preset(name: str) -> "AgentPreset | None":
    return _BUILTINS.get(name)


def generalist() -> AgentPreset:
    return _BUILTINS["generalist"]
