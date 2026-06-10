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
    "shell": "shell: local commands (dir, etc.) — Windows only, no bash/grep/curl",
    "git": (
        "git: run git operations — status, diff, log, branch, add, commit, push, pull, "
        "checkout, stash, show, blame, remote, fetch, merge. "
        "Destructive ops (--force, reset --hard) are blocked. "
        "Always run 'git status' first to understand the repo state."
    ),
    "python_exec": "python_exec: run Python → math, calculations, data processing (don't guess numbers)",
    "search": "search: DuckDuckGo web search → news, docs, general internet info",
    "web": "web: fetch a specific URL as plain text → fast, no JavaScript",
    "browser": (
        "browser: real headless browser (Playwright/Chromium) → JavaScript sites, "
        "job boards (LinkedIn, Indeed, Trabajando.cl), SPAs, pages that block plain HTTP. "
        "action='fetch' loads a URL. action='search' fills a search box and submits. "
        "Prefer search URLs with query params when known."
    ),
    "stock": "stock: real-time stock/crypto price by ticker → ANY stock/share price question",
    "weather": "weather: current weather + forecast for a city → ANY weather question",
    "datetime": "datetime: current date/time → date, time, day of week, time-relative questions",
    "document": (
        "document: create a formatted Word (.docx) or plain text file. "
        "Use for ANY request to 'write a Word doc', 'create a .docx', or 'make a document with formatting'. "
        "Pass title + content with markdown-style headings (# ## ###), **bold**, and - bullet lines."
    ),
    "pptx": (
        "pptx: create a PowerPoint (.pptx) presentation. "
        "Use for ANY request to 'make a presentation', 'create slides', 'a deck', 'una presentación'. "
        "Pass path + a list of slides (each with title, optional subtitle/content bullets/layout/notes). "
        "Themes: light, dark, corporate, sunset. Bullets support **bold**, *italic*, `code`."
    ),
    "gmail": (
        "gmail: read the user's Gmail (read-only) — search, list, read and summarize email. "
        "Use for ANY request about their inbox/correo/email. "
        "action='search' with a Gmail query (e.g. 'is:unread', 'from:x@y.com newer_than:7d'); "
        "action='read' with a message id to get the full body. Cannot send."
    ),
}

_ALL_TOOLS = tuple(_TOOL_DESC.keys())  # includes git, browser, document, all tools
_BROWSER_RULE = (
    "- Use 'browser' for job boards, LinkedIn, Indeed, Trabajando.cl, or any site "
    "that requires JavaScript. Construct search URLs with query params when possible "
    "(e.g. https://cl.indeed.com/jobs?q=...&l=Santiago). "
    "Use 'web' for simple static pages (docs, Wikipedia, APIs).\n"
)


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
    description="Reads/writes code and files, runs shell commands, Python scripts, and git operations.",
    role_intro="You are cortex's coding specialist. You work with local files, the shell, Python, and git.",
    tools=("filesystem", "shell", "git", "python_exec", "document", "pptx"),
    role_rules=(
        "- Stay on the coding/files/scripts task you were given; do not browse the web.\n"
        "- Prefer filesystem over shell for reading/writing files.\n"
        "- For ANY git task: call git() tool directly — NEVER explain commands, execute them.\n"
        "- git add → git(args='add <file>'). git commit → git(args='commit -m \"msg\"'). "
        "git push → git(args='push'). Do it, don't describe it.\n"
        "- CRITICAL: Word / .docx requests → ALWAYS use 'document' tool, NEVER filesystem.\n"
        "- CRITICAL: presentation / slides / .pptx requests → ALWAYS use 'pptx' tool, NEVER filesystem."
    ),
))

_register(AgentPreset(
    name="researcher",
    description=(
        "Searches the web, navigates JS-heavy sites (job boards, LinkedIn, etc.), "
        "fetches URLs, reads docs, and summarizes findings."
    ),
    system_prompt=build_system_prompt(
        "You are cortex's research specialist. You find and synthesize information from the web and local docs.",
        _tool_lines(("search", "web", "browser", "filesystem")),
        "- Use 'search' for general queries, 'web' for simple static URLs, "
        "'browser' for JavaScript-heavy sites and job boards.\n"
        + _BROWSER_RULE
        + "- Cite the source (site/URL) when you state a fact from the web.\n"
        "- For job listings: include the job title, company, and direct link for each result.",
    ),
    tools=("search", "web", "browser", "filesystem"),
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
    name="devops",
    description="Git workflows, shell commands, repo inspection, and file operations.",
    role_intro="You are cortex's devops specialist. You manage repos, run commands, and inspect project state.",
    tools=("git", "shell", "filesystem", "python_exec"),
    role_rules=(
        "- EXECUTE git operations using git() tool — NEVER write instructions for the user to run.\n"
        "- Sequence: git(args='status') → git(args='add <files>') → git(args='commit -m \"msg\"') → git(args='push').\n"
        "- Summarize git log/diff — don't dump raw output unless asked.\n"
        "- Never use --force, reset --hard, or destructive ops without explicit user instruction."
    ),
))

_register(_make(
    name="comms",
    description="Reads and summarizes the user's email (Gmail), and looks things up on the web.",
    role_intro="You are cortex's communications specialist. You read the user's inbox and summarize it.",
    tools=("gmail", "search", "web"),
    role_rules=(
        "- Email / inbox / correo questions → use the 'gmail' tool (read-only).\n"
        "- gmail action='search' with a Gmail query (is:unread, from:, subject:, newer_than:7d); "
        "action='read' with the message id from search results.\n"
        "- Summarize clearly: sender, subject, and the gist. Never invent email content."
    ),
))

_register(_make(
    name="generalist",
    description="Handles any task; has access to every tool. Default for simple requests.",
    role_intro="You are cortex, a helpful AI assistant with access to all local tools.",
    tools=_ALL_TOOLS,
    role_rules=(
        "- Stock prices → 'stock'. Weather → 'weather'. Date/time → 'datetime'. Math → 'python_exec'.\n"
        "- News, current events, docs → 'search'.\n"
        "- CRITICAL: 'word', 'Word', 'docx', '.docx', 'documento Word' → ALWAYS use 'document' tool. "
        "NEVER use filesystem for Word files. Pass title= and content= with # headings, - bullets, **bold**.\n"
        "- CRITICAL: 'presentación', 'presentation', 'slides', 'deck', '.pptx' → ALWAYS use 'pptx' tool. "
        "Pass path= and slides= (list of {title, content:[bullets], layout}). NEVER use filesystem for slides.\n"
        "- Email / inbox / correo / Gmail → use the 'gmail' tool (read-only): "
        "action='search' (Gmail query) then action='read' (message id).\n"
        + _BROWSER_RULE
    ),
))


def all_presets() -> dict[str, AgentPreset]:
    return dict(_BUILTINS)


def get_preset(name: str) -> "AgentPreset | None":
    return _BUILTINS.get(name)


def generalist() -> AgentPreset:
    return _BUILTINS["generalist"]
