"""
cortex.display
──────────────
All terminal visuals. Rich-powered. Dark + cyan/violet aesthetic.
Single source of truth for output — never print() elsewhere.

Core feature: every step shows a live ACTION VERB (READ, WRITE, RUN,
SEARCH, FETCH, ANALYZE) so you see what the agent is doing in real time.
"""

from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Generator

# Windows legacy consoles default to cp1252 and crash on unicode glyphs.
# Force UTF-8 so banner/verbs/emoji render everywhere.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from rich import box
from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ── Palette ─────────────────────────────────────────────────────────────────────

THEME = Theme(
    {
        "agent.name": "bold #7C5CFF",       # violet — agent identity
        "agent.think": "italic #8B8B8B",    # grey — reasoning
        "tool.name": "bold #22D3EE",        # cyan — tool calls
        "tool.input": "#94A3B8",            # slate — arguments
        "tool.output": "#86EFAC",           # green — results
        "tool.error": "bold #F87171",       # red — errors
        "step.number": "bold #7C5CFF",
        "meta": "dim #64748B",
        "success": "bold #4ADE80",
        "warning": "bold #FBBF24",
        "info": "#22D3EE",
        "border": "#27272A",
    }
)

console = Console(theme=THEME, highlight=False)

# ── Action verbs ─────────────────────────────────────────────────────────────────
# Maps (tool, action) → (VERB, icon, style). Drives the live "what is it doing" line.

_VERBS = {
    ("filesystem", "read"):   ("READ",    "◉", "tool.output"),
    ("filesystem", "write"):  ("WRITE",   "✎", "warning"),
    ("filesystem", "mkdir"):  ("MKDIR",   "✚", "warning"),
    ("filesystem", "list"):   ("LIST",    "≣", "info"),
    ("filesystem", "search"): ("SEARCH",  "⌕", "info"),
    ("shell", None):          ("RUN",     "»", "tool.name"),
    ("web", None):            ("FETCH",   "↯", "info"),
    ("browser", "fetch"):     ("BROWSE",  "🌐", "info"),
    ("browser", "search"):    ("BROWSE",  "🌐", "info"),
    ("browser", None):        ("BROWSE",  "🌐", "info"),
    ("search", None):         ("SEARCH",  "⌕", "info"),
    ("stock", None):          ("QUOTE",   "$", "success"),
    ("datetime", None):       ("TIME",    "◷", "info"),
    ("weather", None):        ("WEATHER", "☼", "info"),
    ("python_exec", None):    ("PYTHON",  "▶", "tool.name"),
}


def _verb_for(tool: str, args: dict) -> tuple[str, str, str]:
    action = args.get("action")
    if (tool, action) in _VERBS:
        return _VERBS[(tool, action)]
    if (tool, None) in _VERBS:
        return _VERBS[(tool, None)]
    return ("ANALYZE", "◆", "agent.name")


# Gerund labels for the clean-mode spinner.
_GERUND = {
    "READ": "Reading", "WRITE": "Writing", "LIST": "Listing",
    "SEARCH": "Searching", "RUN": "Running", "FETCH": "Fetching",
    "QUOTE": "Fetching price", "ANALYZE": "Analyzing",
    "TIME": "Checking time", "WEATHER": "Checking weather",
    "MKDIR": "Creating folder", "PYTHON": "Computing",
}


def _clean_label(tool: str, args: dict) -> tuple[str, str, str]:
    """(gerundio, hint, style) — titular corto para modo limpio."""
    verb, icon, style = _verb_for(tool, args)
    gerund = _GERUND.get(verb, "Trabajando")
    # Hint corto según la tool
    hint = ""
    if tool == "filesystem":
        path = str(args.get("path", ""))
        hint = path.replace("\\", "/").rstrip("/").split("/")[-1] if path else ""
    elif tool == "shell":
        hint = str(args.get("command", "")).split()[0] if args.get("command") else ""
    elif tool == "search":
        hint = str(args.get("query", ""))
    elif tool == "web":
        url = str(args.get("url", ""))
        hint = url.split("//")[-1].split("/")[0] if url else ""
    elif tool == "stock":
        hint = str(args.get("symbol", "")).upper()
    elif tool == "weather":
        hint = str(args.get("city", ""))
    if len(hint) > 40:
        hint = hint[:37] + "..."
    return gerund, hint, style


# ── Banner ────────────────────────────────────────────────────────────────────────

_BANNER_LINES = [
    "██████╗ ██████╗ ██████╗ ████████╗███████╗██╗  ██╗",
    "██╔════╝██╔═══██╗██╔══██╗╚══██╔══╝██╔════╝╚██╗██╔╝",
    "██║     ██║   ██║██████╔╝   ██║   █████╗   ╚███╔╝ ",
    "██║     ██║   ██║██╔══██╗   ██║   ██╔══╝   ██╔██╗ ",
    "╚██████╗╚██████╔╝██║  ██║   ██║   ███████╗██╔╝ ██╗",
    " ╚═════╝ ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ╚══════╝╚═╝  ╚═╝",
]
# Fade top→bottom: light violet → brand violet
_BANNER_COLORS = ["#9B7FFF", "#8B6FFF", "#7C5CFF", "#7C5CFF", "#6B4EEE", "#5A3DDD"]


def print_banner(model: str = "", version: str = "0.1.0") -> None:
    console.print()
    for line, color in zip(_BANNER_LINES, _BANNER_COLORS):
        console.print(Text(line, style=f"bold {color}"), justify="center")
    console.print()
    bits = [("local AI agents  ", "dim white"), ("v" + version, "#7C5CFF")]
    if model:
        bits += [("  ·  ", "dim white"), (model, "bold #22D3EE")]
    # Show cumulative money saved vs cloud (best-effort; never break the banner).
    try:
        from cortex import stats
        s = stats.summary()
        if s["total_tokens"] > 0:
            tok = s["total_tokens"]
            tok_str = f"{tok/1_000_000:.1f}M" if tok >= 1_000_000 else (
                f"{tok/1_000:.0f}K" if tok >= 1_000 else str(tok))
            bits += [("  ·  saved ", "dim white"),
                     (f"${s['saved_usd']:.2f}", "bold #4ADE80"),
                     (f" · {tok_str} tok", "dim white")]
        else:
            bits += [("  ·  ", "dim white"), ("$0.00 saved", "bold #4ADE80")]
    except Exception:
        bits += [("  ·  ", "dim white"), ("$0.00 saved", "bold #4ADE80")]
    console.print(Align.center(Text.assemble(*bits)))
    console.print()


# ── Live streaming agent display ────────────────────────────────────────────────────

class AgentDisplay:
    """
    Real-time display of agent reasoning + tool calls.

    Two modes:
      - clean (default): a single live spinner shows the current action verb
        ("Reading a.txt…"). Errors/retries hidden. Only the final RESULT prints.
      - verbose: every step + args + result + errors printed (debugging).
    """

    def __init__(self, task: str, dry_run: bool = False, verbose: bool = False) -> None:
        self.task = task
        self.dry_run = dry_run
        self.verbose = verbose
        self.step = 0
        self.start_time = time.time()
        self._t0: float = 0.0
        self._status = None  # rich Status, clean mode only

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def start(self) -> None:
        """Begin the live spinner (clean mode)."""
        if not self.verbose and self._status is None:
            self._status = console.status(
                Text("Thinking…", style="agent.think"),
                spinner="dots", spinner_style="#7C5CFF",
            )
            self._status.start()

    def stop(self) -> None:
        if self._status is not None:
            self._status.stop()
            self._status = None

    def _update(self, text: Text) -> None:
        if self._status is not None:
            self._status.update(text)

    def print_task(self) -> None:
        tag = "  [DRY RUN]" if self.dry_run else ""
        console.print()
        console.print(
            Panel(
                Text(self.task, style="bold white"),
                title=Text.assemble(("⬡ TASK", "#7C5CFF"), (tag, "bold #FBBF24")),
                border_style="border",
                padding=(0, 2),
            )
        )
        console.print()

    # ── thinking ───────────────────────────────────────────────────────────────
    def on_thinking(self) -> None:
        """Called before each LLM call."""
        self._update(Text("Thinking…", style="agent.think"))

    def thinking(self, thought: str) -> None:
        if self.verbose:
            console.print(Text.assemble(("  ◈ ", "#7C5CFF"), (thought, "agent.think")))

    # ── tool call / result ───────────────────────────────────────────────────────
    def tool_call(self, tool_name: str, args: dict) -> None:
        self.step += 1
        self._t0 = time.time()

        if not self.verbose:
            gerund, hint, style = _clean_label(tool_name, args)
            label = Text.assemble((f"{gerund}", style))
            if hint:
                label.append(f"  {hint}", style="meta")
            label.append("…", style="dim white")
            self._update(label)
            return

        # verbose: full step output
        verb, icon, style = _verb_for(tool_name, args)
        console.print(
            Text.assemble(
                (f"\n  [{self.step:02d}] ", "step.number"),
                (f"{icon} ", style),
                (f"{verb:<8}", style),
                (tool_name, "tool.name"),
                ("  …", "dim white"),
            )
        )
        if args:
            t = Table(box=None, show_header=False, padding=(0, 2, 0, 8), show_edge=False)
            t.add_column(style="meta", no_wrap=True)
            t.add_column(style="tool.input")
            for k, v in args.items():
                val = str(v)
                if len(val) > 80:
                    val = val[:77] + "..."
                t.add_row(f"{k}:", val)
            console.print(t)

    def tool_result(self, tool_name: str, result: str, success: bool = True, truncate: int = 400) -> None:
        if not self.verbose:
            return  # hidden in clean mode
        elapsed = f" {time.time() - self._t0:.2f}s" if self._t0 else ""
        icon = "✓" if success else "✗"
        style = "tool.output" if success else "tool.error"
        shown = result
        if len(result) > truncate:
            shown = result[:truncate] + f"\n      … ({len(result) - truncate} chars truncated)"
        console.print(
            Text.assemble(("      ", ""), (icon + " ", style), (shown, style), (elapsed, "meta"))
        )

    def tool_error(self, tool_name: str, error: str) -> None:
        self.tool_result(tool_name, error, success=False)

    # ── event adapter ────────────────────────────────────────────────────────────
    def handle_event(self, ev) -> None:
        """Drive this display from a runner Event (single-agent path)."""
        kind = ev.kind
        if kind == "thinking":
            self.on_thinking()
        elif kind == "tool_call":
            self.tool_call(ev.tool, ev.args or {})
        elif kind == "tool_result":
            if ev.ok:
                self.tool_result(ev.tool, ev.result or "", success=True)
            else:
                self.tool_error(ev.tool, ev.result or "")
        elif kind == "finished":
            self.final_answer(ev.result or "(no response)")
        elif kind == "error":
            self.tool_error(ev.tool or "agent", ev.result or "error")
        # "started" → nothing; the task panel is already printed.

    def final_answer(self, answer: str) -> None:
        self.stop()
        elapsed = time.time() - self.start_time
        print_result(answer, meta=f"{self.step} steps · {elapsed:.1f}s")

    def dry_run_summary(self, planned: list[dict]) -> None:
        console.print()
        console.print(Rule(Text("DRY RUN — planned tool calls", style="warning"), style="border"))
        t = Table(box=box.SIMPLE, show_header=True, header_style="bold #7C5CFF")
        t.add_column("#", style="meta", width=4)
        t.add_column("Tool", style="tool.name")
        t.add_column("Arguments", style="tool.input")
        for i, call in enumerate(planned, 1):
            args_str = ", ".join(f"{k}={repr(v)[:40]}" for k, v in call.get("args", {}).items())
            t.add_row(str(i), call["tool"], args_str or "—")
        console.print(t)
        console.print(Text("  Run without --dry-run to execute.", style="dim #FBBF24"))
        console.print()


def print_result(answer: str, meta: str = "") -> None:
    """The final green RESULT panel. Shared by single-agent and orchestrator paths."""
    console.print()
    console.print(Rule(style="border"))
    console.print()
    title = Text("✦ RESULT", style="#4ADE80")
    if meta:
        title.append(f"   {meta}", style="meta")
    console.print(Panel(Markdown(answer), title=title, border_style="#4ADE80", padding=(1, 2)))
    console.print()


# ── Multi-agent live display (parallel orchestration) ────────────────────────────────

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Lane:
    """Mutable per-agent state for the live multi-lane table."""

    def __init__(self, name: str, task: str) -> None:
        self.name = name
        self.task = task
        self.status = "queued"          # queued | running | done | error
        self.verb = "Queued"
        self.hint = ""
        self.style = "meta"
        self.steps = 0
        self.start = time.time()
        self.end: float | None = None

    def elapsed(self) -> float:
        return (self.end or time.time()) - self.start


class MultiAgentDisplay:
    """
    Live table with one lane per parallel agent. Workers never call this directly;
    they emit Events into an EventBus and the orchestrator pumps ``apply`` +
    ``refresh`` from the main thread (rich.Live is single-thread).
    """

    def __init__(self, agents: "list[tuple[str, str]]") -> None:
        # agents: list of (preset_name, subtask)
        self.lanes: dict[str, _Lane] = {n: _Lane(n, t) for n, t in agents}
        self._frame = 0
        self._live: "Live | None" = None
        self.start_time = time.time()

    # ── lifecycle ────────────────────────────────────────────────────────────────
    def start(self) -> None:
        console.print()
        console.print(Rule(Text("⬡ PARALLEL TEAM", style="agent.name"), style="border"))
        self._live = Live(self._render(), console=console, refresh_per_second=12, transient=False)
        self._live.start()

    def stop(self) -> None:
        if self._live is not None:
            self._live.update(self._render())
            self._live.stop()
            self._live = None
        console.print()

    def refresh(self) -> None:
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        if self._live is not None:
            self._live.update(self._render())

    # ── event application ────────────────────────────────────────────────────────
    def apply(self, ev) -> None:
        lane = self.lanes.get(ev.agent)
        if lane is None:
            return
        if ev.kind == "started":
            lane.status = "running"
            lane.verb, lane.hint, lane.style = "Thinking", "", "agent.think"
        elif ev.kind == "thinking":
            lane.status = "running"
            lane.verb, lane.hint, lane.style = "Thinking", "", "agent.think"
        elif ev.kind == "tool_call":
            lane.steps += 1
            gerund, hint, style = _clean_label(ev.tool or "", ev.args or {})
            lane.verb, lane.hint, lane.style = gerund, hint, style
        elif ev.kind == "tool_result":
            if not ev.ok:
                lane.style = "warning"  # transient hiccup; loop keeps going
        elif ev.kind == "finished":
            lane.status = "done"
            lane.verb, lane.hint, lane.style = "Done", "", "success"
            lane.end = time.time()
        elif ev.kind == "error":
            lane.status = "error"
            lane.verb, lane.hint, lane.style = "Error", (ev.result or "")[:40], "tool.error"
            lane.end = time.time()

    # ── render ───────────────────────────────────────────────────────────────────
    def _status_icon(self, lane: _Lane) -> Text:
        if lane.status == "done":
            return Text("✓", style="success")
        if lane.status == "error":
            return Text("✗", style="tool.error")
        if lane.status == "running":
            return Text(_SPINNER_FRAMES[self._frame], style="tool.name")
        return Text("·", style="meta")

    def _render(self) -> Table:
        t = Table(box=box.SIMPLE, show_header=True, header_style="bold #7C5CFF",
                  border_style="border", expand=True)
        t.add_column("", width=2)
        t.add_column("Agent", style="agent.name", width=12)
        t.add_column("Action", ratio=2)
        t.add_column("Steps", style="meta", justify="right", width=6)
        t.add_column("Time", style="meta", justify="right", width=8)
        for lane in self.lanes.values():
            action = Text.assemble((lane.verb, lane.style))
            if lane.hint:
                action.append(f"  {lane.hint}", style="meta")
            if lane.status == "running":
                action.append("…", style="dim white")
            t.add_row(
                self._status_icon(lane),
                lane.name,
                action,
                str(lane.steps),
                f"{lane.elapsed():.1f}s",
            )
        return t


# ── Spinner while LLM thinks ─────────────────────────────────────────────────────────

@contextmanager
def thinking_spinner(label: str = "thinking…") -> Generator[None, None, None]:
    with console.status(Text.assemble(("  ", ""), (label, "agent.think")), spinner="dots", spinner_style="#7C5CFF"):
        yield


# ── Tables / helpers ─────────────────────────────────────────────────────────────────

_CLOUD_PROVIDERS: list[dict] = [
    {
        "key_field": "kimi_api_key",
        "label": "Kimi",
        "vendor": "Moonshot AI",
        "site": "moonshot.cn",
        "models": [
            ("moonshot-v1-8k",    "8K ctx"),
            ("moonshot-v1-32k",   "32K ctx"),
            ("moonshot-v1-128k",  "128K ctx  ← recommended"),
        ],
    },
    {
        "key_field": "qwen_api_key",
        "label": "Qwen",
        "vendor": "Alibaba Cloud",
        "site": "dashscope.aliyuncs.com",
        "models": [
            ("qwen-turbo", "1M ctx  · fast"),
            ("qwen-plus",  "1M ctx  · balanced"),
            ("qwen-max",   "1M ctx  · best"),
        ],
    },
    {
        "key_field": "glm_api_key",
        "label": "GLM",
        "vendor": "Zhipu AI",
        "site": "bigmodel.cn",
        "models": [
            ("glm-4-flash", "128K ctx  · fast"),
            ("glm-4-plus",  "128K ctx  · best"),
        ],
    },
    {
        "key_field": "ollama_cloud_api_key",
        "label": "Ollama Cloud",
        "vendor": "Ollama",
        "site": "api.ollama.com",
        "models": [
            ("ollama-cloud/kimi-k2.6:cloud",        "595B · Kimi"),
            ("ollama-cloud/qwen3.5:cloud",           "Qwen 3.5"),
            ("ollama-cloud/glm-5.1:cloud",           "GLM 5.1"),
            ("ollama-cloud/minimax-m3:cloud",        "MiniMax M3"),
            ("ollama-cloud/nemotron-3-super:cloud",  "NVIDIA Nemotron"),
            ("ollama-cloud/gemma4:31b-cloud",        "31B · Google Gemma 4"),
            ("ollama-cloud/gemma3:4b",               "4B · fast · free"),
            ("ollama-cloud/gemma3:27b",              "27B · free"),
            ("ollama-cloud/qwen3-coder-next",        "coding · free?"),
        ],
    },
]


def _is_ollama_cloud_model(name: str) -> bool:
    """Detect models served via Ollama Cloud proxy (tagged :cloud or -cloud)."""
    return name.endswith(":cloud") or "-cloud" in name


def print_models_table(models: list[dict], cfg=None) -> None:
    from rich.rule import Rule

    TOOL_MODELS = {"qwen", "llama3", "llama4", "mistral", "gemma", "phi", "command-r", "deepseek"}

    # Split: real local vs Ollama Cloud proxied (:cloud / -cloud suffix)
    local_models = [m for m in models if not _is_ollama_cloud_model(m.get("name", ""))]
    cloud_proxied = [m for m in models if _is_ollama_cloud_model(m.get("name", ""))]

    # ── LOCAL section ────────────────────────────────────────────
    console.print()
    console.print(Rule(" LOCAL — Ollama ", style="#7C5CFF", align="left"))
    if local_models:
        t = Table(box=box.SIMPLE_HEAD, border_style="border", header_style="bold #22D3EE", padding=(0, 1))
        t.add_column("#",      style="meta",       width=3)
        t.add_column("Model",  style="bold white")
        t.add_column("Size",   style="meta", justify="right")
        t.add_column("Tools",  justify="center", width=6)
        for i, m in enumerate(local_models, 1):
            name = m.get("name", "")
            sz   = m.get("size", 0)
            size = f"{sz / 1e9:.1f} GB" if sz else "—"
            ok   = any(k in name.lower() for k in TOOL_MODELS)
            active = "  [bold #4ADE80]← active[/]" if cfg and f"ollama/{name}" == cfg.model else ""
            t.add_row(
                str(i),
                name + active,
                size,
                Text("✓" if ok else "·", style="success" if ok else "meta"),
            )
        console.print(t)
    else:
        console.print("  [dim]No local models. Pull one: ollama pull qwen3:8b[/]")
    console.print(f"  [dim]Switch: /model <name or #>[/]")

    # ── CLOUD section ────────────────────────────────────────────
    console.print()
    console.print(Rule(" CLOUD — providers ", style="#7C5CFF", align="left"))
    console.print()
    cloud_letter_idx = 0  # a, b, c... across all configured cloud providers
    for p in _CLOUD_PROVIDERS:
        key_val = getattr(cfg, p["key_field"], None) if cfg else None
        configured = bool(key_val)
        icon  = "[bold #4ADE80]●[/]" if configured else "[dim #94A3B8]○[/]"
        state = "[bold #4ADE80]configured[/]" if configured else "[dim]no key — add to ~/.cortex/config.toml[/]"
        console.print(f"  {icon}  [bold]{p['label']}[/]  [dim]{p['vendor']} · {p['site']}[/]  {state}")

        if p["label"] == "Ollama Cloud":
            # Collect all models: hardcoded + any extra from local Ollama :cloud tags
            known_ids = {model_name for model_name, _ in p["models"]}
            all_oc_models: list[tuple[str, str]] = list(p["models"])
            for m in cloud_proxied:
                local_name = m.get("name", "")
                cloud_id = f"ollama-cloud/{local_name}"
                if cloud_id not in known_ids:
                    all_oc_models.append((cloud_id, "local proxy"))
            for model_name, hint in all_oc_models:
                letter = chr(ord("a") + cloud_letter_idx) if configured else " "
                cloud_letter_idx += 1 if configured else 0
                lbl = f"[bold #94A3B8]{letter}[/]" if configured else " "
                active = "  [bold #4ADE80]← active[/]" if cfg and model_name == cfg.model else ""
                # Show short name (strip ollama-cloud/ prefix for readability)
                short = model_name.replace("ollama-cloud/", "")
                console.print(f"    {lbl}  [#22D3EE]{short}[/]  [dim]{hint}[/]{active}")
        else:
            if configured:
                for model_name, hint in p["models"]:
                    letter = chr(ord("a") + cloud_letter_idx)
                    cloud_letter_idx += 1
                    lbl = f"[bold #94A3B8]{letter}[/]"
                    active = "  [bold #4ADE80]← active[/]" if cfg and model_name == cfg.model else ""
                    console.print(f"    {lbl}  [#22D3EE]{model_name}[/]  [dim]{hint}[/]{active}")
        console.print()
    console.print(f"  [dim]Use cloud model: /model <name> or cortex run -m <name> 'task'[/]")
    console.print()


def print_run_history(runs: list[dict]) -> None:
    t = Table(
        title=Text("Recent runs", style="bold #7C5CFF"),
        box=box.SIMPLE_HEAD, border_style="border", header_style="bold #22D3EE",
    )
    t.add_column("Time", style="meta", width=20)
    t.add_column("Task", style="white")
    t.add_column("Steps", style="tool.name", justify="right", width=6)
    t.add_column("Duration", style="meta", justify="right", width=10)
    t.add_column("Model", style="dim white", width=18)
    for r in runs[-15:]:
        task = r.get("task", "")
        t.add_row(
            r.get("timestamp", "")[:19],
            task[:60] + ("…" if len(task) > 60 else ""),
            str(r.get("tool_calls", 0)),
            f"{r.get('duration_s', 0):.1f}s",
            r.get("model", ""),
        )
    console.print(t)


def print_error(msg: str) -> None:
    console.print(f"\n  [tool.error]✗ {msg}[/tool.error]\n")


def print_success(msg: str) -> None:
    console.print(f"\n  [success]✓ {msg}[/success]\n")


def print_info(msg: str) -> None:
    console.print(f"  [info]ℹ {msg}[/info]")


def print_warning(msg: str) -> None:
    console.print(f"  [warning]⚠ {msg}[/warning]")
