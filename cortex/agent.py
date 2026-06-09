"""
cortex.agent
------------
Compatibility entry point for running a task. The real work lives in
cortex.agents.*  -- this module keeps the historical run() / _build_tool_registry
surface so the CLI and any callers stay stable.

run(): dry-run does a single planning preview; otherwise delegates to the
orchestrator, which decides single vs parallel agents.
"""

from __future__ import annotations

import json
from typing import Any

from cortex.agents import llm, presets
from cortex.agents.orchestrator import orchestrate
from cortex.config import Settings
from cortex.display import AgentDisplay, thinking_spinner
from cortex.tools.registry import ToolRegistry

# Back-compat: the generalist prompt used to live here as SYSTEM_PROMPT.
SYSTEM_PROMPT = presets.generalist().system_prompt

def _build_tool_registry(cfg: Settings) -> tuple[list[dict], dict[str, Any]]:
    """Returns (schemas, executor_map) for the full tool set.

    Thin shim over ToolRegistry — the single source of truth for tools is
    cortex.tools.registry. Add new tools there, not here.
    """
    reg = ToolRegistry.default(cfg)
    schemas = reg.schemas()
    executors: dict[str, Any] = {name: reg.executor(name) for name in reg.names()}
    return schemas, executors

def _dry_run(task: str, cfg: Settings, cloud: bool, display: AgentDisplay) -> str:
    """Single planning pass: show the tool calls the generalist would make."""
    generalist = presets.generalist()
    model = generalist.model or cfg.effective_model(cloud=cloud)
    schemas = ToolRegistry.default(cfg).schemas()
    messages = [
        {"role": "system", "content": generalist.system_prompt},
        {"role": "user", "content": task},
    ]
    try:
        with thinking_spinner("planning…"):
            resp = llm.complete_with_tools(model, messages, schemas, cfg)
    except Exception as e:
        display.tool_error("llm", str(e))
        return f"[ERROR] LLM call failed: {e}"
    planned = [
        {"tool": tc.function.name, "args": json.loads(tc.function.arguments or "{}")}
        for tc in (resp.choices[0].message.tool_calls or [])
    ]
    display.dry_run_summary(planned)
    return "(dry run — nothing executed)"

def run(task: str, cfg: Settings, cloud: bool = False, dry_run: bool = False,
        verbose: bool = False, single: bool = False, session_context: str = "") -> str:
    try:
        import litellm  # noqa: F401
    except ImportError:
        raise RuntimeError("litellm not installed. Run: pip install -e .")

    if dry_run:
        display = AgentDisplay(task=task, dry_run=True, verbose=verbose)
        display.print_task()
        return _dry_run(task, cfg, cloud, display)

    return orchestrate(task, cfg, cloud=cloud, verbose=verbose, force_single=single,
                       session_context=session_context)