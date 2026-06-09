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

_DOC_EXTS = (".docx", ".doc")


def _maybe_redirect_to_document(name: str, args: dict, registry: ToolRegistry) -> tuple[str, dict]:
    """If model calls filesystem(write, *.docx) → silently redirect to document tool.

    Models like qwen2.5-coder ignore the 'use document tool' rule and always reach
    for filesystem. This intercept catches it at execution time and fixes it transparently.
    """
    if name != "filesystem":
        return name, args
    if args.get("action") != "write":
        return name, args
    path = str(args.get("path", ""))
    if not any(path.lower().endswith(ext) for ext in _DOC_EXTS):
        return name, args
    if "document" not in registry:
        return name, args
    # Redirect: map filesystem write args → document args
    new_args = {
        "path": path,
        "content": args.get("content", ""),
        "title": args.get("title", ""),
    }
    return "document", new_args


_GIT_ACTION_RE = None  # compiled lazily


def _extract_git_commands(content: str) -> "list[str] | None":
    """Scan model text for git commands it's EXPLAINING instead of executing.

    Matches lines like:
      git add .
      git commit -m "msg"
      git push origin main
    inside or outside code blocks.
    Returns list of git arg strings (e.g. ['add .', 'commit -m "msg"', 'push'])
    or None if no actionable git sequence found.
    """
    import re
    global _GIT_ACTION_RE
    if _GIT_ACTION_RE is None:
        _GIT_ACTION_RE = re.compile(
            r'`{0,3}git\s+((?:add|commit|push|pull|status|stash|checkout|merge|branch|log)[^\n`]{0,200})`{0,3}',
            re.IGNORECASE,
        )
    matches = _GIT_ACTION_RE.findall(content)
    if not matches:
        return None
    # Only intercept if there's at least add+commit or commit+push (real workflow)
    joined = " ".join(matches).lower()
    has_commit = "commit" in joined
    has_push = "push" in joined
    if not (has_commit or has_push):
        return None
    # Clean up each match
    cmds = []
    for m in matches:
        cmd = m.strip().strip("`").strip()
        if cmd:
            cmds.append(cmd)
    return cmds if cmds else None


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
                    name, args = _maybe_redirect_to_document(name, args, sub)
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

            # Intercept: model explained git commands instead of executing them.
            # Extract and run them now so the user doesn't have to do it manually.
            git_cmds = _extract_git_commands(msg.content or "")
            git_exec = sub.executor("git")
            if git_cmds and git_exec and not force_answer:
                results = []
                for git_args in git_cmds:
                    emit(Event(agent=preset.name, kind="tool_call",
                               tool="git", args={"args": git_args}))
                    t0 = time.time()
                    try:
                        res = str(git_exec({"args": git_args}))
                        ok = not res.startswith(_FAIL_PREFIXES)
                    except Exception as e:
                        res, ok = f"[ERROR] {type(e).__name__}: {e}", False
                    emit(Event(agent=preset.name, kind="tool_result",
                               tool="git", result=res, ok=ok))
                    steps.append({
                        "tool": "git", "args": {"args": git_args},
                        "result_preview": res[:200], "success": ok,
                        "duration_s": round(time.time() - t0, 2),
                    })
                    results.append(f"`git {git_args}`:\n{res}")
                # Feed results back and get a real summary
                messages.append({"role": "assistant", "content": msg.content,
                                  "tool_calls": []})
                messages.append({"role": "user",
                                  "content": "Git commands executed. Results:\n"
                                             + "\n\n".join(results)
                                             + "\n\nSummarize what was done."})
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

            # Intercept: filesystem(write, *.docx) → document()
            name, args = _maybe_redirect_to_document(name, args, sub)

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
