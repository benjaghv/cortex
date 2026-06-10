"""
cortex CLI
──────────
Terminal-first local AI agents powered by Ollama.
"""

from __future__ import annotations

import json
from typing import Optional

import typer
from rich.prompt import Prompt

from cortex import __version__
from cortex.config import CONFIG_FILE, RUNS_DIR, Settings
from cortex.display import (
    console,
    print_banner,
    print_error,
    print_info,
    print_models_table,
    print_run_history,
    print_success,
    print_warning,
)

_SUGGESTED_MODELS = [
    # (model,                    size,    ram,     description)
    ("qwen2.5-coder:1.5b", "1 GB",  "4 GB+",  "fast · low-end hardware"),
    ("qwen2.5-coder:7b",   "5 GB",  "8 GB+",  "coding · recommended ⭐"),
    ("qwen3:8b",           "5 GB",  "8 GB+",  "general · good reasoning"),
    ("deepseek-r1:8b",     "5 GB",  "8 GB+",  "research · chain-of-thought"),
    ("llama3.2:3b",        "2 GB",  "6 GB+",  "light · fast · general"),
    ("gemma3:4b",          "3 GB",  "6 GB+",  "multilingual · Google"),
    ("phi4:14b",           "9 GB",  "16 GB+", "high quality · efficient"),
    ("deepseek-r1:14b",    "9 GB",  "16 GB+", "strong reasoning"),
]


def _print_model_suggestions() -> None:
    from rich import box
    from rich.table import Table
    t = Table(box=box.SIMPLE_HEAD, border_style="border",
              header_style="bold #22D3EE", padding=(0, 1))
    t.add_column("Model",       style="bold white")
    t.add_column("Size",        style="meta",    justify="right")
    t.add_column("RAM",         style="meta",    justify="right")
    t.add_column("Description", style="dim white")
    for model, size, ram, desc in _SUGGESTED_MODELS:
        t.add_row(model, size, ram, desc)
    console.print(t)
    console.print("  [dim]Pull:  ollama pull <model>[/]")
    console.print("  [dim]More:  https://ollama.com/search[/]")
    console.print()


app = typer.Typer(
    name="cortex",
    help="Local AI agents with tools, in your terminal — powered by Ollama.",
    no_args_is_help=False,
    invoke_without_command=True,
    add_completion=True,
)


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context):
    """Launch chat when no subcommand given."""
    if ctx.invoked_subcommand is None:
        chat(model=None, cloud=False, verbose=False)


@app.command()
def run(
    task: str = typer.Argument(..., help="Task for the agent."),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model, e.g. ollama/qwen3:8b"),
    cloud: bool = typer.Option(False, "--cloud", help="Use fallback_model from config."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show planned tool calls, don't run them."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every step, args, and errors."),
    single: bool = typer.Option(False, "--single", "--no-parallel",
                                help="Force one generalist agent (skip parallel orchestration)."),
    no_banner: bool = typer.Option(False, "--no-banner"),
):
    """Run the agent on a single task (auto-splits across parallel agents when it helps)."""
    cfg = Settings.load()
    if model:
        cfg.model = model
    if not no_banner:
        print_banner(model=cfg.effective_model(cloud=cloud), version=__version__)
    from cortex.agent import run as agent_run
    agent_run(task=task, cfg=cfg, cloud=cloud, dry_run=dry_run, verbose=verbose, single=single)


@app.command()
def voice(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override model."),
    cloud: bool = typer.Option(False, "--cloud", help="Use fallback_model from config."),
    lang: Optional[str] = typer.Option(None, "--lang", "-l", help="Speech language, e.g. es-ES, en-US."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    no_banner: bool = typer.Option(False, "--no-banner"),
):
    """Speak a task instead of typing it — transcribes your voice, then runs it."""
    from cortex import voice as voice_input
    cfg = Settings.load()
    if model:
        cfg.model = model
    if not no_banner:
        print_banner(model=cfg.effective_model(cloud=cloud), version=__version__)

    spoken = voice_input.listen(cfg, language=lang)
    if not spoken:
        return
    console.print(f"  [dim]🎤 dijiste:[/] [bold white]{spoken}[/]\n")
    from cortex.agent import run as agent_run
    agent_run(task=spoken, cfg=cfg, cloud=cloud, dry_run=False, verbose=verbose)


@app.command()
def chat(
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    cloud: bool = typer.Option(False, "--cloud"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show every step and errors."),
):
    """Interactive multi-task session. 'exit' to quit."""
    import httpx

    cfg = Settings.load()
    if model:
        cfg.model = model
    state = {"verbose": verbose}

    def _fetch_models() -> list[str]:
        return [m["name"] for m in _fetch_models_full()]

    def _fetch_models_full() -> list[dict]:
        """Full model dicts from Ollama (name + size + …), for the models table."""
        try:
            with httpx.Client(timeout=4.0) as c:
                r = c.get(f"{cfg.ollama_base_url}/api/tags")
                r.raise_for_status()
                return r.json().get("models", [])
        except Exception:
            return []

    def _ollama_help() -> None:
        console.print()
        console.print("  [bold #F87171]✗ Can't reach Ollama[/] at "
                      f"[dim]{cfg.ollama_base_url}[/]")
        console.print()
        console.print("  [bold]Fix:[/]")
        console.print("  [#22D3EE]1.[/] Open a terminal and run:  [bold]ollama serve[/]")
        console.print("  [#22D3EE]2.[/] Or on Windows: look for the Ollama icon in the system tray")
        console.print("     and make sure it says 'Running'.")
        console.print("  [#22D3EE]3.[/] If Ollama isn't installed: [bold]https://ollama.com/download[/]")
        console.print("  [#22D3EE]4.[/] After starting, pull a model:  [bold]ollama pull qwen2.5-coder:7b[/]")
        console.print()
        console.print(f"  [dim]Expected URL: {cfg.ollama_base_url}  "
                      "(change with CORTEX_OLLAMA_BASE_URL or ~/.cortex/config.toml)[/]")
        console.print()

    def _prompt_label() -> str:
        short = cfg.model.replace("ollama/", "")
        return f"[bold #7C5CFF]▶[/] [dim #22D3EE]{short}[/] "

    def _handle_slash(cmd: str) -> bool:
        """Handle /commands. Returns True if handled (skip agent)."""
        parts = cmd.strip().split(None, 1)
        slug = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        # /models — list available
        if slug in ("/models", "/model") and not arg:
            local = _fetch_models_full()  # full dicts → keeps the Size column
            if not local and not getattr(cfg, "ollama_cloud_api_key", None):
                _ollama_help()
                return True
            # Show table even if local list is empty — cloud section still useful
            print_models_table(local, cfg=cfg)
            return True

        # /model <name|number> — switch
        if slug == "/model" and arg:
            # ── Cloud model: direct switch, no local lookup needed ──
            _CLOUD_PREFIXES = ("ollama-cloud/", "moonshot-", "qwen-", "glm-", "openai/")
            if any(arg.startswith(p) for p in _CLOUD_PREFIXES):
                cfg.model = arg
                print_success(f"Switched to {cfg.model}")
                return True

            # ── Single letter → cloud model by index (a=first, b=second…) ──
            if len(arg) == 1 and arg.isalpha() and arg.islower():
                from cortex.display import _CLOUD_PROVIDERS, _is_ollama_cloud_model
                ordered: list[str] = []
                for p in _CLOUD_PROVIDERS:
                    key_val = getattr(cfg, p["key_field"], None)
                    if not key_val:
                        continue
                    if p["label"] == "Ollama Cloud":
                        # same order as display: hardcoded + local proxy extras
                        avail_all = _fetch_models()
                        cloud_px = [m for m in avail_all if _is_ollama_cloud_model(m)]
                        known = {mn for mn, _ in p["models"]}
                        for mn, _ in p["models"]:
                            ordered.append(mn)
                        for local_name in cloud_px:
                            cid = f"ollama-cloud/{local_name}"
                            if cid not in known:
                                ordered.append(cid)
                    else:
                        for mn, _ in p["models"]:
                            ordered.append(mn)
                idx = ord(arg) - ord("a")
                if 0 <= idx < len(ordered):
                    cfg.model = ordered[idx]
                    print_success(f"Switched to {cfg.model}")
                else:
                    print_error(f"No cloud model '{arg}'. Run /models to see letters.")
                return True

            available = _fetch_models()
            # Try by number
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(available):
                    cfg.model = f"ollama/{available[idx]}"
                    print_success(f"Switched to {cfg.model}")
                else:
                    print_error(f"No model #{arg}. Run /models to list.")
                return True
            # Fuzzy match by name substring
            from cortex.display import _is_ollama_cloud_model
            local_only = [m for m in available if not _is_ollama_cloud_model(m)]
            cloud_proxied = [m for m in available if _is_ollama_cloud_model(m)]

            # Exact local (non-cloud) models first
            matches = [m for m in local_only if arg.lower() in m.lower()]
            if len(matches) == 1:
                cfg.model = f"ollama/{matches[0]}"
                print_success(f"Switched to {cfg.model}")
                return True
            if len(matches) > 1:
                print_warning(f"Ambiguous local: {', '.join(matches)}. Be more specific.")
                return True

            # :cloud proxied models from local Ollama
            cloud_local_matches = [m for m in cloud_proxied if arg.lower() in m.lower()]
            if len(cloud_local_matches) == 1:
                cfg.model = f"ollama/{cloud_local_matches[0]}"
                print_success(f"Switched to {cfg.model}")
                return True
            if len(cloud_local_matches) > 1:
                print_warning(f"Ambiguous cloud: {', '.join(cloud_local_matches)}. Be more specific.")
                return True

            # Static cloud provider models (Kimi, Qwen, GLM + ollama-cloud/ direct API)
            from cortex.display import _CLOUD_PROVIDERS
            all_static = [
                full_name
                for provider in _CLOUD_PROVIDERS
                for full_name, _hint in provider["models"]
                if full_name  # skip empty
            ]
            static_matches = [m for m in all_static if arg.lower() in m.lower()]
            if len(static_matches) == 1:
                cfg.model = static_matches[0]
                print_success(f"Switched to {cfg.model}")
            elif len(static_matches) > 1:
                print_warning(f"Ambiguous: {', '.join(static_matches)}. Be more specific.")
            else:
                print_error(f"No model matching '{arg}'. Run /models to list.")
            return True

        # /repo [path] — set active git repo for this session
        if slug == "/repo":
            import os
            from pathlib import Path
            if not arg:
                cur = state.get("repo") or os.getcwd()
                console.print(f"\n  [dim]Active repo:[/] [bold #22D3EE]{cur}[/]\n")
                return True
            expanded = str(Path(os.path.expandvars(arg).replace("~", str(Path.home()))).resolve())
            if not Path(expanded).exists():
                print_error(f"Path not found: {expanded}")
                return True
            if not (Path(expanded) / ".git").exists():
                print_warning(f"{expanded} doesn't look like a git repo (no .git folder). Set anyway?")
            state["repo"] = expanded
            print_success(f"Active repo → {expanded}")
            return True

        # /verbose — toggle detail mode
        if slug == "/verbose":
            state["verbose"] = not state["verbose"]
            print_info(f"Verbose {'ON — showing every step' if state['verbose'] else 'OFF — clean mode'}")
            return True

        # /help
        if slug in ("/help", "/?"):
            console.print()
            console.print("  [bold #7C5CFF]Commands:[/]")
            console.print("  [#22D3EE]/models[/]          — list Ollama models")
            console.print("  [#22D3EE]/model <name>[/]    — switch model (partial name ok)")
            console.print("  [#22D3EE]/model <number>[/]  — switch local model by number")
            console.print("  [#22D3EE]/model <letter>[/]  — switch cloud model by letter (a, b, c…)")
            console.print("  [#22D3EE]/repo <path>[/]     — set active git repo for this session")
            console.print("  [#22D3EE]/repo[/]            — show current repo path")
            console.print("  [#22D3EE]/verbose[/]         — toggle verbose / clean mode")
            console.print("  [#22D3EE]/voice[/]           — dictate your next prompt by speaking")
            console.print("  [#22D3EE]/dry-run <task>[/]  — plan without executing")
            console.print("  [#22D3EE]exit[/]             — quit")
            console.print()
            return True

        # /dry-run <task>
        if slug == "/dry-run":
            if arg:
                from cortex.agent import run as agent_run
                agent_run(task=arg, cfg=cfg, cloud=cloud, dry_run=True, verbose=state["verbose"])
            else:
                print_warning("Usage: /dry-run <task>")
            return True

        print_warning(f"Unknown command '{slug}'. Type /help.")
        return True

    print_banner(model=cfg.effective_model(cloud=cloud), version=__version__)
    print_info("Type a task or question — /help for commands — exit to quit.")

    # ── Startup check: warn if Ollama unreachable and model is local ──────────
    _is_local_model = cfg.model.startswith("ollama/") or "/" not in cfg.model
    if _is_local_model:
        try:
            with httpx.Client(timeout=2.0) as c:
                c.get(f"{cfg.ollama_base_url}/api/tags").raise_for_status()
        except Exception:
            _has_cloud_key = bool(getattr(cfg, "ollama_cloud_api_key", None))
            console.print()
            console.print("  [bold #FACC15]⚠  Ollama is not running[/]  "
                          f"[dim]({cfg.ollama_base_url})[/]")
            if _has_cloud_key:
                console.print()
                console.print("  You have an Ollama Cloud API key — use a cloud model instead:")
                console.print("  [bold]  /model a[/]   [dim]← kimi-k2.6:cloud[/]")
                console.print("  [bold]  /model b[/]   [dim]← qwen3.5:cloud[/]")
                console.print("  [bold]  /models[/]    [dim]← see all options[/]")
            else:
                console.print("  Fix: open a new terminal →  [bold]ollama serve[/]")
                console.print("  No Ollama? Download: [bold]https://ollama.com/download[/]")
                console.print("  Then pull a model: [bold]ollama pull qwen2.5-coder:7b[/]")
            console.print()

    console.print()

    from cortex.agent import run as agent_run
    from rich.text import Text as RichText

    _MAX_SESSION_TURNS = 8
    _MAX_SESSION_CHARS = 3000
    session_turns: list[dict] = []   # {task, result} per turn this session
    state["repo"] = None             # active repo path for git operations

    def _build_session_context() -> str:
        if not session_turns and not state.get("repo"):
            return ""
        lines = ["CURRENT SESSION (this conversation — use to maintain context):"]
        if state.get("repo"):
            lines.append(f"  Active repo (use this path as cwd for ALL git operations): {state['repo']}")
        for i, t in enumerate(session_turns, 1):
            short_result = t["result"][:200].replace("\n", " ")
            if len(t["result"]) > 200:
                short_result += "…"
            lines.append(f"  [{i}] User: {t['task']}")
            lines.append(f"       Assistant: {short_result}")
        return "\n".join(lines)

    def _session_indicator() -> None:
        if not session_turns:
            return
        turns = len(session_turns)
        total_chars = sum(len(t["task"]) + len(t["result"]) for t in session_turns)
        ctx_window = 32_000 * 4  # rough chars estimate for 32K token window
        pct = min(total_chars / ctx_window * 100, 100)
        filled = int(pct / 5)   # 20 blocks = 100%
        bar = "█" * filled + "░" * (20 - filled)
        bar_color = "#4ADE80" if pct < 60 else ("#FACC15" if pct < 85 else "#F87171")
        console.print(
            f"  [dim]session[/] [bold]{turns}[/] [dim]turns ·[/] "
            f"[dim]~{total_chars//1000:.1f}K chars[/]  "
            f"[{bar_color}]{bar}[/]  [dim]{pct:.0f}%[/]",
        )
        console.print()

    while True:
        try:
            task = Prompt.ask(_prompt_label())
            task = task.strip()
            if not task:
                continue
            if task.lower() in ("exit", "quit", "q"):
                print_info("Bye. 👋")
                break
            if task.startswith("/"):
                # /voice → dictate a prompt by speaking, then run it as a task.
                if task.lower().split()[0] == "/voice":
                    from cortex import voice
                    spoken = voice.listen(cfg)
                    if not spoken:
                        continue
                    task = spoken
                    console.print(f"  [dim]🎤 dijiste:[/] [bold white]{task}[/]\n")
                    # fall through → run `task` through the agent below
                else:
                    _handle_slash(task)
                    continue
            ctx = _build_session_context()
            result = agent_run(task=task, cfg=cfg, cloud=cloud, dry_run=False,
                               verbose=state["verbose"], session_context=ctx)
            # Store turn (trim oldest if over limit)
            session_turns.append({"task": task, "result": result or ""})
            if len(session_turns) > _MAX_SESSION_TURNS:
                session_turns.pop(0)
            _session_indicator()
        except KeyboardInterrupt:
            console.print()
            print_info("Interrupted. 👋")
            break


@app.command()
def models():
    """List local Ollama models + available cloud providers."""
    import httpx
    cfg = Settings.load()
    print_banner(version=__version__)
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{cfg.ollama_base_url}/api/tags")
            r.raise_for_status()
            ml = r.json().get("models", [])
        if not ml:
            console.print()
            console.print("  [bold #FACC15]No local models found.[/]  Pull one first:\n")
            _print_model_suggestions()
            return
        print_models_table(ml, cfg=cfg)
    except Exception:
        console.print()
        console.print(f"  [bold #F87171]✗ Can't reach Ollama[/] at [dim]{cfg.ollama_base_url}[/]")
        console.print()
        console.print("  [bold]Fix:[/]")
        console.print("  [#22D3EE]1.[/] Open a NEW terminal and run:  [bold]ollama serve[/]")
        console.print("  [#22D3EE]2.[/] Windows: check Ollama icon in system tray → must say 'Running'")
        console.print("  [#22D3EE]3.[/] Not installed? Download: [bold]https://ollama.com/download[/]")
        console.print()
        console.print("  [bold]Recommended models to pull after starting Ollama:[/]")
        console.print()
        _print_model_suggestions()
        console.print(f"  [dim]Config URL: {cfg.ollama_base_url}  "
                      "(override: CORTEX_OLLAMA_BASE_URL env var)[/]")
        console.print()


@app.command()
def agents():
    """List the built-in agent presets and their tools."""
    from rich import box
    from rich.table import Table
    from rich.text import Text

    from cortex.agents import presets as P

    print_banner(version=__version__)
    t = Table(
        title=Text("Agent presets", style="bold #7C5CFF"),
        box=box.SIMPLE_HEAD, border_style="border", header_style="bold #22D3EE",
    )
    t.add_column("Agent", style="bold #7C5CFF")
    t.add_column("Tools", style="tool.name")
    t.add_column("Role", style="white")
    for p in P.all_presets().values():
        t.add_row(p.name, ", ".join(p.tools), p.description)
    console.print(t)
    print_info("The orchestrator routes subtasks to these automatically. "
               "Force one agent with: cortex run --single 'task'")


@app.command()
def history(limit: int = typer.Option(15, "--limit", "-n")):
    """Show recent run history."""
    print_banner(version=__version__)
    files = sorted(RUNS_DIR.glob("*.json"), reverse=True)[:limit]
    if not files:
        print_info("No runs yet. Try: cortex run 'hello'")
        return
    runs = []
    for f in files:
        try:
            runs.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    print_run_history(runs)


@app.command()
def stats():
    """Show cumulative tokens processed + estimated cloud cost comparison."""
    from cortex import stats as S
    try:
        from cortex.config import Settings
        cfg = Settings.load()
        in_p  = cfg.cost_ref_input_per_1m
        out_p = cfg.cost_ref_output_per_1m
    except Exception:
        in_p, out_p = S._DEFAULT_IN, S._DEFAULT_OUT
    print_banner(version=__version__)
    s = S.summary(in_p, out_p)
    console.print()
    console.print(f"  [bold #4ADE80]${s['saved_usd']:.2f}[/] [dim]estimated saved vs cloud[/]")
    console.print(f"  [bold #22D3EE]{s['total_tokens']:,}[/] [dim]tokens processed[/]  "
                  f"[meta]{s['prompt_tokens']:,} in · {s['completion_tokens']:,} out[/]")
    console.print(f"  [bold #7C5CFF]{s['runs']}[/] [dim]runs[/]")
    console.print()
    if in_p >= 2.5:
        _label = "Claude Sonnet / GPT-4.1" if in_p >= 2.9 else "GPT-4o"
    elif in_p >= 0.8:
        _label = "Claude Haiku / GPT-4o-mini"
    else:
        _label = "custom"
    console.print(f"  [dim]Reference: ${in_p}/1M input · ${out_p}/1M output "
                  f"({_label} equivalent)[/]")
    console.print(f"  [dim]Formula: (input_tok × ${in_p} + output_tok × ${out_p}) / 1,000,000[/]")
    console.print(f"  [dim]Override in ~/.cortex/config.toml: "
                  f"cost_ref_input_per_1m / cost_ref_output_per_1m[/]")
    console.print()


@app.command()
def memory(
    clear: bool = typer.Option(False, "--clear", help="Erase all stored memory."),
    limit: int = typer.Option(10, "--limit", "-n"),
):
    """Show (or clear) what cortex remembers from past sessions."""
    from cortex import memory as M
    print_banner(version=__version__)
    if clear:
        M.clear()
        print_success("Memory cleared.")
        return
    items = M.recent(limit)
    if not items:
        print_info("No memory yet. Cortex remembers after your first task.")
        return
    console.print()
    for it in items:
        when = it.get("ts", "")[:19].replace("T", " ")
        console.print(f"  [meta]{when}[/]  [white]{it.get('task','')}[/]")
        console.print(f"     [dim #86EFAC]→ {it.get('result','')}[/]")
    console.print()


@app.command()
def config(
    show: bool = typer.Option(False, "--show", help="Print current config."),
    init: bool = typer.Option(False, "--init", help="Create default config file."),
):
    """Manage cortex configuration."""
    cfg = Settings.load()
    if init:
        cfg.save_default()
        print_success(f"Config at {CONFIG_FILE}")
        return
    if show:
        safe = {k: v for k, v in cfg.model_dump().items()
                if "key" not in k and "token" not in k}
        console.print_json(json.dumps(safe, default=str))
        return
    print_info(f"Config file: {CONFIG_FILE}")
    print_info(f"Run logs:    {RUNS_DIR}")
    print_info("--init to create config, --show to print it.")


@app.command()
def version():
    """Show cortex version."""
    from rich.text import Text
    console.print(Text.assemble(("cortex ", "dim white"), (__version__, "bold #7C5CFF")))


def main():
    app()


if __name__ == "__main__":
    main()
