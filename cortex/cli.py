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
    try:
        agent_run(task=task, cfg=cfg, cloud=cloud, dry_run=dry_run, verbose=verbose, single=single)
    except KeyboardInterrupt:
        console.print()
        print_warning("⏹ Detenido por el usuario.")
        raise typer.Exit(130)


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

        # /connect [gmail|outlook] [path/to/client_secret.json] — OAuth in-session
        if slug == "/connect":
            cargs = arg.split()
            service = cargs[0] if cargs else "gmail"
            secret = next((a for a in cargs[1:] if a.endswith(".json") or "/" in a or "\\" in a), None)
            _run_connect(cfg, service, secret)
            return True

        # /disconnect <gmail|outlook> [email] — remove a connected account
        if slug == "/disconnect":
            cargs = arg.split()
            svc = (cargs[0] if cargs else "gmail").lower()
            target_email = cargs[1] if len(cargs) > 1 else None
            if svc in ("outlook", "microsoft", "office365", "o365"):
                from cortex.integrations import microsoft_auth as ms
                target = target_email or ms.active_account()
                if target and ms.disconnect(target):
                    print_success(f"Desconectada: {target}")
                else:
                    print_warning("No había cuenta de Outlook conectada.")
            else:
                from cortex.integrations import google_auth
                target = target_email or google_auth.active_account()
                if target and google_auth.disconnect(target):
                    print_success(f"Desconectada: {target}")
                else:
                    print_warning("No había cuenta de Gmail conectada.")
            return True

        # /account [email] — show or switch the active Gmail / Outlook account
        if slug in ("/account", "/accounts"):
            from cortex.integrations import google_auth
            try:
                from cortex.integrations import microsoft_auth
            except Exception:
                microsoft_auth = None
            if arg:
                # Switch in whichever store knows this email.
                done = False
                try:
                    google_auth.set_active(arg)
                    print_success(f"Cuenta activa (Gmail) → {arg}")
                    done = True
                except google_auth.GoogleAuthError:
                    pass
                if not done and microsoft_auth:
                    try:
                        microsoft_auth.set_active(arg)
                        print_success(f"Cuenta activa (Outlook) → {arg}")
                        done = True
                    except Exception:
                        pass
                if not done:
                    print_error(f"La cuenta '{arg}' no está conectada.")
                return True

            g_accs = google_auth.list_accounts()
            g_active = google_auth.active_account()
            m_accs = microsoft_auth.list_accounts() if microsoft_auth else []
            m_active = microsoft_auth.active_account() if microsoft_auth else None
            if not g_accs and not m_accs:
                print_info("No hay cuentas conectadas. Conéctate con:  cortex connect gmail  o  cortex connect outlook")
                return True
            console.print()
            if g_accs:
                console.print("  [bold #7C5CFF]Gmail[/]")
                for a in g_accs:
                    mark = "  [bold #4ADE80]← activa[/]" if a == g_active else ""
                    console.print(f"    [#22D3EE]•[/] {a}{mark}")
            if m_accs:
                console.print("  [bold #7C5CFF]Outlook[/]")
                for a in m_accs:
                    mark = "  [bold #4ADE80]← activa[/]" if a == m_active else ""
                    console.print(f"    [#22D3EE]•[/] {a}{mark}")
            console.print("  [dim]Cambiar: /account <email>[/]\n")
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
            console.print("  [#22D3EE]/connect gmail[/]      — connect a Gmail account (browser OAuth)")
            console.print("  [#22D3EE]/connect outlook[/]    — connect an Outlook / Microsoft 365 account (device code)")
            console.print("  [#22D3EE]/disconnect outlook[/] — remove the connected Outlook account")
            console.print("  [#22D3EE]/account[/] \\[email]    — show or switch active Gmail / Outlook account")
            console.print("  [#22D3EE]/dry-run <task>[/]     — plan without executing")
            console.print("  [#22D3EE]exit[/]                — quit")
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
            # Typed a terminal command (e.g. "cortex connect gmail") inside the chat?
            # Rewrite to the slash form so it runs here instead of going to the agent.
            if task.lower().startswith("cortex "):
                rest = task[len("cortex "):].strip()
                first = rest.split()[0].lower() if rest else ""
                _SLASHABLE = {"connect", "account", "accounts", "models", "model", "voice"}
                if first in _SLASHABLE:
                    task = "/" + rest
                else:
                    print_warning(f"'cortex {rest}' es un comando de terminal — sal del chat (exit) "
                                  "y córrelo ahí, o usa los slash-commands (/help).")
                    continue
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
            try:
                result = agent_run(task=task, cfg=cfg, cloud=cloud, dry_run=False,
                                   verbose=state["verbose"], session_context=ctx)
            except KeyboardInterrupt:
                # Ctrl+C DURING a task → stop that task, stay in chat.
                console.print()
                print_warning("⏹ Tarea detenida. Sigues en el chat (Ctrl+C de nuevo o 'exit' para salir).")
                continue
            # Store turn (trim oldest if over limit)
            session_turns.append({"task": task, "result": result or ""})
            if len(session_turns) > _MAX_SESSION_TURNS:
                session_turns.pop(0)
            _session_indicator()
        except KeyboardInterrupt:
            # Ctrl+C at the prompt (no task running) → leave chat.
            console.print()
            print_info("Bye. 👋")
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


def _print_gmail_setup_guide() -> None:
    """Clear, in-terminal first-time guide shown when there's no OAuth client yet."""
    from rich.panel import Panel
    from rich.text import Text
    from cortex.config import CREDENTIALS_DIR

    creds_dir = str(CREDENTIALS_DIR)
    t = Text()
    t.append("Para leer tu Gmail, Google pide un permiso OAuth. Se hace ", "white")
    t.append("UNA sola vez", "bold #FACC15")
    t.append(".\n", "white")
    t.append("⚠ HAZ TODO EN EL MISMO PROYECTO", "bold #FACC15")
    t.append(" — mira el selector de proyecto arriba-izquierda y no lo cambies.\n\n", "white")

    def step(n: str, *parts):
        t.append(f"  {n}  ", "bold #22D3EE")
        for txt, st in parts:
            t.append(txt, st)
        t.append("\n")

    step("1.", ("Abre ", "white"), ("https://console.cloud.google.com", "#7C5CFF"),
         ("  y crea o elige ", "white"), ("UN proyecto", "bold"), (".", "white"))
    step("2.", ("Te abro la pestaña ", "white"), ("Clientes", "bold"),
         (" → ", "white"), ("Crear cliente de OAuth", "bold"),
         (" → tipo ", "white"), ("App de escritorio", "bold #4ADE80"),
         (" → ", "white"), ("Descargar JSON", "bold"), (".", "white"))
    t.append("       (Si pide configurar la pantalla de consentimiento: Branding → nombre + tu correo.)\n",
             "dim white")
    step("3.", ("Guarda ese JSON en esta carpeta (o en Descargas):", "white"))
    t.append(f"         {creds_dir}\n", "bold #4ADE80")
    t.append("       En cuanto aparezca ", "dim white")
    t.append("lo detecto solo", "bold #4ADE80")
    t.append(" y te abro las pestañas exactas para habilitar la API\n", "dim white")
    t.append("       y agregarte como usuario de prueba. Quedará en modo ", "dim white")
    t.append("Prueba", "bold dim white")
    t.append(" (el acceso se renueva cada ~7 días). ✅", "dim white")

    console.print()
    console.print(Panel(t, title=Text("Conectar Gmail — configuración inicial (una vez)", style="#7C5CFF"),
                        border_style="#7C5CFF", padding=(1, 2)))
    console.print()


def _guided_gmail_setup(cfg) -> None:
    """Open the Google Cloud pages and watch the credentials folder for the JSON.

    The only manual part is clicking through the console (it needs the user's Google
    login). Cortex opens the right pages and auto-detects the file the moment it lands,
    then continues to the OAuth sign-in — no paths to copy, no commands to re-run.
    """
    import sys
    import time
    import webbrowser

    from rich.text import Text

    from cortex.config import CREDENTIALS_DIR
    from cortex.integrations import google_auth

    _print_gmail_setup_guide()

    if not sys.stdin.isatty():  # non-interactive → don't hang waiting
        return
    try:
        ans = Prompt.ask("  ¿Abro las páginas de Google Cloud y espero el archivo por ti?",
                         choices=["s", "n"], default="s", show_default=False).strip().lower()
    except Exception:
        return
    if ans not in ("", "s", "si", "sí", "y", "yes"):
        return

    # First, only the Clients page so they create the Desktop OAuth client.
    try:
        webbrowser.open("https://console.cloud.google.com/auth/clients")
    except Exception:
        pass

    console.print(f"\n  [dim]Cuando descargues el JSON, déjalo en[/] [bold]{CREDENTIALS_DIR}[/] "
                  "[dim](o en Descargas).[/]")
    console.print("  [dim]Lo detecto solo. Ctrl+C para cancelar.[/]\n")

    found = None
    try:
        with console.status(Text("Vigilando… esperando el client_secret.json", style="agent.think"),
                            spinner="dots", spinner_style="#7C5CFF"):
            for _ in range(150):  # ~5 min at 2s intervals
                found = google_auth._autodiscover_client_secret()
                if found:
                    break
                time.sleep(2)
    except KeyboardInterrupt:
        console.print("\n  [dim]Cancelado. Cuando tengas el archivo, corre de nuevo:[/] "
                      "[bold]cortex connect gmail[/]\n")
        return

    if not found:
        console.print("  [dim]No detecté el archivo a tiempo. Déjalo en la carpeta y corre de nuevo:[/] "
                      "[bold]cortex connect gmail[/]\n")
        return

    # cortex now knows the project from the JSON → open the EXACT per-project pages,
    # eliminating the "which project am I in?" confusion.
    pid = google_auth.project_id_of(found)
    print_success(f"Detecté el cliente del proyecto '{pid or '?'}'.")
    if pid:
        api_url = f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={pid}"
        users_url = f"https://console.cloud.google.com/auth/audience?project={pid}"
        for url in (api_url, users_url):
            try:
                webbrowser.open(url)
            except Exception:
                pass
        console.print(f"\n  Te abrí 2 pestañas del proyecto [bold]{pid}[/]. Haz esto y vuelve:")
        console.print("    [#22D3EE]1.[/] En [bold]Gmail API[/] → clic [bold]Habilitar[/].")
        console.print("    [#22D3EE]2.[/] En [bold]Público → Usuarios de prueba[/] → agrega tu correo de Gmail.")
        console.print(f"       [dim]API:[/] {api_url}")
        console.print(f"       [dim]Testers:[/] {users_url}\n")
        try:
            Prompt.ask("  Cuando hayas hecho ambas, presiona Enter para conectar", default="")
        except Exception:
            pass

    _run_connect(cfg, "gmail", str(found))


def _run_connect(cfg, service: str, client_secret: "str | None") -> None:
    """Shared connect flow used by `cortex connect` and the chat /connect command."""
    svc = service.lower()
    if svc in ("outlook", "microsoft", "office365", "o365"):
        _run_connect_outlook(cfg)
        return
    if svc not in ("gmail", "google"):
        print_error(f"Servicio '{service}' no soportado todavía. Disponible: gmail, outlook")
        return
    from cortex.integrations import google_auth
    # No OAuth client anywhere yet → launch the guided assistant (open pages + watch).
    if (not client_secret and not google_auth.client_secret_path(cfg)
            and not google_auth._autodiscover_client_secret()):
        _guided_gmail_setup(cfg)
        return
    try:
        console.print("\n  [dim]Abriendo el navegador… elige tu cuenta de Google y acepta.[/]\n")
        email = google_auth.connect(cfg, client_secret_src=client_secret)
        print_success(f"Conectado: {email}  (cuenta activa)")
        console.print("  [dim]Token guardado en ~/.cortex/credentials/ — nunca se commitea.[/]")
        console.print("  [dim]Prueba:[/] [bold]resume mis últimos correos sin leer[/]\n")
    except google_auth.MissingDepsError as e:
        print_error(str(e))
    except google_auth.MissingClientSecretError:
        _print_gmail_setup_guide()
    except google_auth.GoogleAuthError as e:
        print_error(str(e))
    except Exception as e:
        msg = str(e)
        # Most common post-auth failure: the Gmail API isn't enabled in that project.
        if "accessNotConfigured" in msg or "has not been used in project" in msg:
            pid = google_auth.project_id_of(client_secret or google_auth.client_secret_path(cfg))
            url = (f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={pid}"
                   if pid else "https://console.cloud.google.com/apis/library/gmail.googleapis.com")
            console.print(f"\n  [bold #F87171]✗ La Gmail API no está habilitada[/] en el proyecto "
                          f"[bold]{pid or '?'}[/].")
            console.print(f"  Habilítala aquí → [bold]{url}[/]")
            console.print("  Clic [bold]Habilitar[/], espera 1-2 min y corre de nuevo "
                          "[bold]cortex connect gmail[/].\n")
        else:
            print_error(f"{type(e).__name__}: {e}")


def _save_config_key(key: str, value: str) -> None:
    """Upsert a single key into ~/.cortex/config.toml, preserving the rest."""
    import toml
    data = {}
    if CONFIG_FILE.exists():
        try:
            data = toml.load(CONFIG_FILE)
        except Exception:
            data = {}
    data[key] = value
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(toml.dumps(data), encoding="utf-8")


def _guided_outlook_setup(cfg) -> "str | None":
    """Walk the user through the one-time Azure app registration, paste the client id
    inline, save it to config, and return it. None if cancelled."""
    import re
    import sys
    import webbrowser
    from rich.panel import Panel
    from rich.text import Text

    t = Text()
    t.append("Para conectar Outlook necesitas registrar una app en Azure. Se hace ", "white")
    t.append("UNA sola vez", "bold #FACC15")
    t.append(" y es gratis. Te abro las páginas y me pegas el ID al final.\n\n", "white")

    def step(n, *parts):
        t.append(f"  {n}  ", "bold #22D3EE")
        for txt, st in parts:
            t.append(txt, st)
        t.append("\n")

    step("1.", ("New registration", "bold"), (" → nombre cualquiera (ej. 'cortex').", "white"))
    step("  ", ("Supported account types: ", "white"),
         ("'cualquier organización + cuentas personales'", "bold"), (".", "white"))
    step("2.", ("Authentication", "bold"), (" → abajo, ", "white"),
         ("'Allow public client flows' = Yes", "bold"), (" → Save.", "white"))
    step("3.", ("API permissions", "bold"), (" → Add → Microsoft Graph → Delegated → agrega:\n", "white"),
         ("       Mail.Read, Mail.Send, Mail.ReadWrite, offline_access, User.Read", "bold"))
    step("4.", ("En el ", "white"), ("Overview", "bold"),
         (" copia el ", "white"), ("Application (client) ID", "bold"),
         (" y pégalo aquí abajo.", "white"))

    console.print()
    console.print(Panel(t, title=Text("⬡ Conectar Outlook — configuración inicial (una vez)",
                                       style="bold #22D3EE"),
                        border_style="border", padding=(0, 2)))

    if not sys.stdin.isatty():
        print_info("Cuando tengas el client ID, ponlo en config y corre: cortex connect outlook")
        return None

    try:
        ans = Prompt.ask("  ¿Abro la página de Azure (App registrations) por ti?",
                         choices=["s", "n"], default="s", show_default=False).strip().lower()
    except Exception:
        return None
    if ans in ("", "s", "si", "sí", "y", "yes"):
        for url in (
            "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/CreateApplicationBlade",
            "https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade",
        ):
            try:
                webbrowser.open(url)
                break
            except Exception:
                continue

    console.print("\n  [dim]Cuando tengas el Application (client) ID, pégalo. Ctrl+C para cancelar.[/]")
    guid_re = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
    try:
        for _ in range(3):
            cid = Prompt.ask("  Application (client) ID").strip()
            if guid_re.match(cid):
                break
            print_warning("Eso no parece un client ID (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx). Reintenta.")
        else:
            print_error("No ingresaste un client ID válido. Corre de nuevo: cortex connect outlook")
            return None
    except (KeyboardInterrupt, EOFError):
        console.print("\n  [dim]Cancelado.[/]\n")
        return None

    _save_config_key("microsoft_client_id", cid)
    cfg.microsoft_client_id = cid
    print_success("Client ID guardado en ~/.cortex/config.toml")
    return cid


def _run_connect_outlook(cfg) -> None:
    """Guided, step-by-step device-code login for Outlook / Microsoft 365."""
    import webbrowser
    from rich.panel import Panel
    from rich.text import Text
    from cortex.integrations import microsoft_auth as ms

    # No Azure app yet → run the guided setup (opens pages + paste id inline).
    if not getattr(cfg, "microsoft_client_id", None):
        if not _guided_outlook_setup(cfg):
            return

    def show(msg: str) -> None:
        try:
            webbrowser.open("https://microsoft.com/devicelogin")
        except Exception:
            pass
        console.print()
        console.print(Panel(Text(msg, style="bold white"),
                            title=Text("⬡ Inicia sesión en Microsoft", style="bold #22D3EE"),
                            border_style="border", padding=(0, 2)))
        console.print("  [dim]Te abrí la página. Pega el código, elige tu cuenta y aprueba. "
                      "Esperando…[/]\n")

    try:
        email = ms.connect(cfg, on_message=show)
        print_success(f"Conectado: {email}  (cuenta activa)")
        console.print("  [dim]Token guardado en ~/.cortex/credentials/microsoft/ — nunca se commitea.[/]")
        console.print("  [dim]Prueba:[/] [bold]resume mis últimos correos de Outlook[/]\n")
    except ms.MissingClientIdError as e:
        print_error(str(e))
    except ms.MicrosoftAuthError as e:
        print_error(str(e))
    except Exception as e:
        print_error(f"{type(e).__name__}: {e}")


@app.command()
def connect(
    service: str = typer.Argument("gmail", help="Service to connect (gmail / outlook)."),
    client_secret: Optional[str] = typer.Option(
        None, "--client-secret", "-c",
        help="Path to your Google Cloud OAuth client_secret.json (gmail, first time only)."),
):
    """Connect a third-party account (opens your browser). Persistent + switchable."""
    cfg = Settings.load()
    print_banner(version=__version__)
    _run_connect(cfg, service, client_secret)


@app.command()
def accounts(
    use: Optional[str] = typer.Option(None, "--use", "-u", help="Switch the active account to this email."),
):
    """List connected accounts (or switch the active one with --use <email>)."""
    from cortex.integrations import google_auth
    print_banner(version=__version__)
    if use:
        try:
            google_auth.set_active(use)
            print_success(f"Cuenta activa → {use}")
        except google_auth.GoogleAuthError as e:
            print_error(str(e))
        return

    accs = google_auth.list_accounts()
    active = google_auth.active_account()
    # Microsoft / Outlook accounts (separate store).
    try:
        from cortex.integrations import microsoft_auth
        ms_accs = microsoft_auth.list_accounts()
        ms_active = microsoft_auth.active_account()
    except Exception:
        ms_accs, ms_active = [], None

    console.print()
    if not accs and not ms_accs:
        console.print("  [dim]No hay cuentas conectadas.[/]  Conéctate con:  "
                      "[bold]cortex connect gmail[/]  o  [bold]cortex connect outlook[/]\n")
        return
    if accs:
        console.print("  [bold #7C5CFF]Cuentas de Google (Gmail)[/]")
        for a in accs:
            mark = "  [bold #4ADE80]← activa[/]" if a == active else ""
            console.print(f"    [#22D3EE]•[/] {a}{mark}")
    if ms_accs:
        console.print("  [bold #7C5CFF]Cuentas de Microsoft (Outlook)[/]")
        for a in ms_accs:
            mark = "  [bold #4ADE80]← activa[/]" if a == ms_active else ""
            console.print(f"    [#22D3EE]•[/] {a}{mark}")
    console.print("\n  [dim]Reconectar/cambiar:[/] [bold]cortex connect outlook[/]  ·  "
                  "[dim]quitar:[/] [bold]cortex disconnect outlook <email>[/]\n")


@app.command()
def disconnect(
    service: str = typer.Argument("gmail", help="Service (gmail / outlook)."),
    email: Optional[str] = typer.Argument(None, help="Account to remove (default: active)."),
):
    """Disconnect a connected account (deletes its local token)."""
    svc = service.lower()
    if svc in ("outlook", "microsoft", "office365", "o365"):
        from cortex.integrations import microsoft_auth as ms
        target = email or ms.active_account()
        if not target:
            print_warning("No hay ninguna cuenta de Outlook conectada.")
            return
        if ms.disconnect(target):
            print_success(f"Desconectada: {target}")
        else:
            print_warning(f"La cuenta '{target}' no estaba conectada.")
        return

    from cortex.integrations import google_auth
    if svc not in ("gmail", "google"):
        print_error(f"Servicio '{service}' no soportado.")
        return
    target = email or google_auth.active_account()
    if not target:
        print_warning("No hay ninguna cuenta conectada.")
        return
    if google_auth.disconnect(target):
        print_success(f"Desconectada: {target}")
    else:
        print_warning(f"La cuenta '{target}' no estaba conectada.")


# (label, import name, pip packages to install if missing, system-lib note)
_TOOL_DEPS = [
    ("Word .docx  (document)",      "docx",               [],            ""),
    ("PowerPoint .pptx  (pptx)",    "pptx",               [],            ""),
    ("Gmail  (gmail)",              "googleapiclient",    [],            ""),
    ("Outlook  (outlook)",          "msal",               [],            ""),
    ("Voice transcription",         "speech_recognition", [],            ""),
    ("Voice microphone  (pyaudio)", "pyaudio",            ["pyaudio"],
     "Mac: brew install portaudio  ·  Linux: sudo apt install portaudio19-dev"),
    ("Headless browser  (browser)", "playwright",         ["playwright"],
     "after install, downloads Chromium automatically"),
]


@app.command()
def setup(
    install: bool = typer.Option(False, "--install", "-i", help="Install any missing tool dependencies."),
):
    """Check every tool's dependencies on this device — with --install, install the missing ones."""
    import importlib.util
    import subprocess
    import sys

    print_banner(version=__version__)
    console.print()
    console.print("  [bold #7C5CFF]Tool dependencies[/]\n")

    missing_pkgs: list[str] = []
    need_playwright = False
    all_ok = True
    for label, mod, pkgs, note in _TOOL_DEPS:
        ok = importlib.util.find_spec(mod) is not None
        mark = "[#4ADE80]✓[/]" if ok else "[#F87171]✗[/]"
        hint = "" if ok or not note else f"  [dim]{note}[/]"
        console.print(f"    {mark}  {label}{hint}")
        if not ok:
            all_ok = False
            missing_pkgs += pkgs
            if mod == "playwright":
                need_playwright = True
    console.print()

    if all_ok:
        print_success("Todo listo — cada herramienta tiene sus dependencias en este equipo.")
        return

    if not install:
        console.print("  Instala lo que falta con:  [bold]cortex setup --install[/]\n")
        return

    if missing_pkgs:
        console.print(f"  [dim]pip install {' '.join(missing_pkgs)}[/]")
        r = subprocess.run([sys.executable, "-m", "pip", "install", *missing_pkgs])
        if r.returncode != 0:
            print_error("pip falló. Si es por SSL/certificados, reintenta con: "
                        f"{sys.executable} -m pip install --use-feature=truststore {' '.join(missing_pkgs)}")
            print_warning("Si pyaudio falla, instala PortAudio primero (ver la nota de arriba).")

    if need_playwright and importlib.util.find_spec("playwright") is not None:
        console.print("  [dim]playwright install chromium[/]")
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])

    console.print()
    print_info("Listo. Corre [bold]cortex setup[/] de nuevo para confirmar el estado.")


@app.command()
def version():
    """Show cortex version."""
    from rich.text import Text
    console.print(Text.assemble(("cortex ", "dim white"), (__version__, "bold #7C5CFF")))


def main():
    app()


if __name__ == "__main__":
    main()
