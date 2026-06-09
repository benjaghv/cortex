"""
cortex.agents.runner
────────────────────
Runs ONE agent's ReAct loop. Reason → Act (tool) → Observe → repeat until the
model answers. Emits Events instead of touching the console, so the same loop
works for a single live agent or many parallel ones.

Migrated from agent._react_loop; behavior (loop guard, unknown-tool handling,
force-answer) is preserved.
"""

from __future__ import annotations

import json
import time

from cortex.agents import llm
from cortex.agents.preset import AgentPreset
from cortex.config import Settings
from cortex.events import Emitter, Event
from cortex.tools.registry import ToolRegistry

_FAIL_PREFIXES = ("[ERROR]", "[BLOCKED]", "[EXIT", "[TIMEOUT]", "[GUARD]")


def run_agent(
    preset: AgentPreset,
    task: str,
    registry: ToolRegistry,
    cfg: Settings,
    emit: Emitter,
    cloud: bool = False,
    memory_block: str = "",
) -> str:
    """Drive one agent to an answer. Returns the final text (or an [ERROR] string)."""
    model = preset.model or cfg.effective_model(cloud=cloud)
    sub = registry.subset(preset.tools)
    schemas = sub.schemas()

    system = preset.system_prompt
    if memory_block:
        system = f"{system}\n\n{memory_block}"
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    recent_calls: list[str] = []
    steps: list[dict] = []
    force_answer = False
    tool_count = 0

    emit(Event(agent=preset.name, kind="started", result=task))

    for _iteration in range(cfg.max_iterations):
        emit(Event(agent=preset.name, kind="thinking"))
        try:
            if force_answer:
                resp = llm.complete_no_tools(model, messages, cfg)
                force_answer = False
            else:
                resp = llm.complete_with_tools(model, messages, schemas, cfg)
        except Exception as e:
            err = f"[ERROR] LLM call failed: {e}"
            emit(Event(agent=preset.name, kind="error", result=err, ok=False))
            return err

        msg = resp.choices[0].message

        if not msg.tool_calls:
            final = llm.humanize(msg.content or "(no response)")
            emit(Event(agent=preset.name, kind="finished", result=final))
            return final

        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            emit(Event(agent=preset.name, kind="tool_call", tool=name, args=args))
            tool_count += 1
            t0 = time.time()

            executor = sub.executor(name)

            # Unknown / out-of-scope tool → inject error + force a text answer.
            if executor is None:
                result = (
                    f"The tool '{name}' is not available to you. Available: "
                    f"{', '.join(sub.names())}. Using the information you already gathered, "
                    "write the final answer to the user's question now, in the same language the user used."
                )
                emit(Event(agent=preset.name, kind="tool_result", tool=name,
                           result=f"tool '{name}' not available", ok=False))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                force_answer = True
                break

            # Loop guard: identical call repeated → force answer from gathered data.
            fp = f"{name}:{json.dumps(args, sort_keys=True)}"
            recent_calls.append(fp)
            if recent_calls.count(fp) >= 2 and recent_calls[-1] == fp:
                emit(Event(agent=preset.name, kind="tool_result", tool=name,
                           result="loop detected — forcing answer", ok=False))
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": "You already have the information from previous tool "
                                            "results above. Now write the final answer to the user's "
                                            "original question, in the same language the user used."})
                force_answer = True
                break

            try:
                result = str(executor(args))
                ok = not result.startswith(_FAIL_PREFIXES)
            except Exception as e:
                result, ok = f"[ERROR] {type(e).__name__}: {e}", False

            emit(Event(agent=preset.name, kind="tool_result", tool=name, result=result, ok=ok))
            steps.append({
                "tool": name, "args": args, "result_preview": result[:200],
                "success": ok, "duration_s": round(time.time() - t0, 2),
            })
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    timeout = "⚠ Se alcanzó el máximo de iteraciones. Resultados parciales arriba."
    emit(Event(agent=preset.name, kind="finished", result=timeout))
    return timeout
