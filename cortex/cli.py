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
        try:
            with httpx.Client(timeout=4.0) as c:
                r = c.get(f"{cfg.ollama_base_url}/api/tags")
                r.raise_for_status()
                return [m["name"] for m in r.json().get("models", [])]
        except Exception:
            return []

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
            available = _fetch_models()
            if not available:
                print_error("Can't reach Ollama.")
                return True
            local = [{"name": m} for m in available]
            print_models_table(local, cfg=cfg)
            return True

        # /model <name|number> — switch
        if slug == "/model" and arg:
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
            matches = [m for m in available if arg.lower() in m.lower()]
            if len(matches) == 1:
                cfg.model = f"ollama/{matches[0]}"
                print_success(f"Switched to {cfg.model}")
            elif len(matches) > 1:
                print_warning(f"Ambiguous: {', '.join(matches)}. Be more specific.")
            else:
                print_error(f"No model matching '{arg}'. Run /models to list.")
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
            console.print("  [#22D3EE]/model <number>[/]  — switch model by number")
            console.print("  [#22D3EE]/verbose[/]         — toggle verbose / clean mode")
            console.print("  [#22D3EE]/dry-run <tarea>[/] — planear sin ejecutar")
            console.print("  [#22D3EE]exit[/]             — salir")
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
    console.print()

    from cortex.agent import run as agent_run

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
                _handle_slash(task)
                continue
            agent_run(task=task, cfg=cfg, cloud=cloud, dry_run=False, verbose=state["verbose"])
        except KeyboardInterrupt:
            console.print()
            print_info("Interrupted. 👋")
            break


@app.command()
def models():
    """List local Ollama models."""
    import httpx
    cfg = Settings.load()
    print_banner(version=__version__)
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(f"{cfg.ollama_base_url}/api/tags")
            r.raise_for_status()
            ml = r.json().get("models", [])
        if not ml:
            print_warning("No models. Pull one: ollama pull qwen3:8b")
            return
        print_models_table(ml, cfg=cfg)
        print_info("Use cloud: cortex run -m <model-name> 'your task'")
    except Exception:
        print_error(f"Can't reach Ollama at {cfg.ollama_base_url}. Start it: ollama serve")


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
    """Show cumulative tokens processed locally + money saved vs cloud."""
    from cortex import stats as S
    print_banner(version=__version__)
    s = S.summary()
    console.print()
    console.print(f"  [bold #4ADE80]${s['saved_usd']:.2f}[/] [dim]saved vs cloud[/]")
    console.print(f"  [bold #22D3EE]{s['total_tokens']:,}[/] [dim]total tokens[/] "
                  f"[meta]({s['prompt_tokens']:,} in · {s['completion_tokens']:,} out)[/]")
    console.print(f"  [bold #7C5CFF]{s['runs']}[/] [dim]runs[/]")
    console.print()
    print_info("Reference prices configurable: cost_ref_input_per_1m / _output_per_1m")


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
