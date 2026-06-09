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


def _extract_text_tool_calls(content: str) -> "list[dict] | None":
    """Parse tool calls embedded in model text (models without native tool_calls support).

    Handles the format some models (e.g. gemma3) emit as plain JSON:
      [{"type":"search","function":{"name":"search","parameters":{"query":"..."}}}]
    or a plain dict for a single call. Returns list of {"name": str, "args": dict} or None.
    """
    if not content:
        return None
    stripped = content.strip()
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data:
        return None

    calls: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            return None  # mixed list — bail out
        fn = item.get("function") or {}
        name = fn.get("name") or item.get("name") or item.get("tool")
        args = (
            fn.get("parameters")
            or fn.get("arguments")
            or item.get("parameters")
            or item.get("arguments")
            or {}
        )
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if name and isinstance(args, dict):
            calls.append({"name": str(name), "args": args})

    return calls if calls else None


def run_agent(
    preset: AgentPreset,
    task: str,
    registry: ToolRegistry,
    cfg: Settings,
    emit: Emitter,
    cloud: bool = False,
    memory_block: str = "",
    session_context: str = "",
) -> str:
    """Drive one agent to an answer. Returns the final text (or an [ERROR] string)."""
    model = preset.model or cfg.effective_model(cloud=cloud)
    sub = registry.subset(preset.tools)
    schemas = sub.schemas()

    system = preset.system_prompt
    if memory_block:
        system = f"{system}\n\n{memory_block}"
    if session_context:
        system = f"{system}\n\n{session_context}"
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
            # Some models (gemma3, etc.) don't support native tool_calls — they emit
            # the call as a JSON array/object in content.  Detect and execute it.
            text_calls = _extract_text_tool_calls(msg.content or "")
            if text_calls:
                tool_results: list[str] = []
                for tc_data in text_calls:
                    name = tc_data["name"]
                    args = tc_data["args"]
                    emit(Event(agent=preset.name, kind="tool_call", tool=name, args=args))
                    tool_count += 1
                    t0 = time.time()
                    executor = sub.executor(name)
                    if executor is None:
                        result = (
                            f"Tool '{name}' is not available. Available: "
                            f"{', '.join(sub.names())}."
                        )
                        ok = False
                    else:
                        fp = f"{name}:{json.dumps(args, sort_keys=True)}"
                        recent_calls.append(fp)
                        try:
                            result = str(executor(args))
                            ok = not result.startswith(_FAIL_PREFIXES)
                        except Exception as e:
                            result, ok = f"[ERROR] {type(e).__name__}: {e}", False
                    emit(Event(agent=preset.name, kind="tool_result", tool=name,
                               result=result, ok=ok))
                    steps.append({
                        "tool": name, "args": args,
                        "result_preview": result[:200],
                        "success": ok,
                        "duration_s": round(time.time() - t0, 2),
                    })
                    tool_results.append(f"[{name}]: {result}")
                # Feed results back as context, then force text answer
                messages.append({"role": "assistant",
                                  "content": "\n\n".join(tool_results)})
                messages.append({"role": "user",
                                  "content": "Using the tool results above, answer my "
                                             "original question. Be direct and concise."})
                force_answer = True
                continue

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

    timeout = "⚠ Max iterations reached. Partial results above."
    emit(Event(agent=preset.name, kind="finished", result=timeout))
    return timeout
