"""
cortex.agents.orchestrator
───────────────────────────
The brain. Given one user task it:

  1. Decides single vs parallel (cheap heuristic, then an LLM planner).
  2. single   → runs the generalist agent (back-compat with old `cortex run`).
  3. parallel → runs specialist agents concurrently (threads), live multi-lane,
                then synthesizes one final answer.

Robust by design: any planner failure falls back to the single generalist path,
so the worst case is exactly today's behavior.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from cortex import memory, stats
from cortex.agents import llm, presets
from cortex.agents.preset import AgentPreset
from cortex.agents.runner import run_agent
from cortex.config import RUNS_DIR, Settings
from cortex.display import (
    AgentDisplay,
    MultiAgentDisplay,
    print_info,
    print_result,
)
from cortex.events import EventBus
from cortex.tools.registry import ToolRegistry


# ── planner ──────────────────────────────────────────────────────────────────────────

def _planner_system() -> str:
    lines = "\n".join(
        f"  - {p.name}: {p.description}" for p in presets.all_presets().values()
    )
    return (
        "You are a task planner for a team of specialist AI agents. Given a user task, "
        "decide whether ONE agent should handle it, or whether it splits into INDEPENDENT "
        "subtasks that can run in PARALLEL.\n"
        "CRITICAL RULES:\n"
        "- Split ONLY when subtasks are TRULY INDEPENDENT (no subtask needs another's result).\n"
        "- If one subtask must CREATE or WRITE a file using results from another subtask, "
        "use 'single' — the generalist handles fetch+write in sequence.\n"
        "- If the task says 'crea/guarda/escribe ... con [datos]', use 'single'.\n"
        "- Prefer 'single' unless parallelism clearly helps (e.g. fetch stock prices AND "
        "fetch weather at the same time, with no dependency between them).\n"
        "Available agents:\n"
        f"{lines}\n"
        "Return STRICT JSON only, no prose:\n"
        '{"mode":"single"|"parallel","subtasks":[{"agent":"<name>","task":"<text>"}]}\n'
        "For 'single', subtasks has exactly one item using the 'generalist' agent."
    )


def _looks_simple(task: str) -> bool:
    """Cheap pre-check: short, single-clause tasks skip the planner entirely."""
    t = task.strip().lower()
    if len(t.split()) <= 6:
        return True
    connectors = (" y ", " and ", "; ", ", luego", ", después", " then ")
    return not any(c in t for c in connectors)


def _plan(task: str, cfg: Settings, cloud: bool) -> "list[tuple[AgentPreset, str]]":
    """Return list of (preset, subtask). One item = single mode."""
    generalist = presets.generalist()
    if _looks_simple(task):
        return [(generalist, task)]

    model = cfg.planner_model or cfg.effective_model(cloud=cloud)
    try:
        raw = llm.complete_json(model, _planner_system(), task, cfg)
        data = json.loads(raw)
    except Exception:
        return [(generalist, task)]

    if data.get("mode") != "parallel":
        return [(generalist, task)]

    subtasks = data.get("subtasks") or []
    jobs: list[tuple[AgentPreset, str]] = []
    for st in subtasks:
        if not isinstance(st, dict):
            continue
        name = str(st.get("agent", "")).strip()
        sub = str(st.get("task", "")).strip()
        if not sub:
            continue
        preset = presets.get_preset(name) or generalist
        jobs.append((preset, sub))
        if len(jobs) >= cfg.max_parallel_agents:
            break

    # Need at least 2 real parallel jobs, else just run single.
    if len(jobs) < 2:
        return [(generalist, task)]
    return jobs


# ── parallel execution ─────────────────────────────────────────────────────────────────

def _unique_lane_ids(jobs: "list[tuple[AgentPreset, str]]") -> list[str]:
    """coder, researcher, coder → coder, researcher, coder#2 (stable, readable)."""
    seen: dict[str, int] = {}
    ids: list[str] = []
    for preset, _ in jobs:
        seen[preset.name] = seen.get(preset.name, 0) + 1
        n = seen[preset.name]
        ids.append(preset.name if n == 1 else f"{preset.name}#{n}")
    return ids


def _run_parallel(task: str, jobs, registry, cfg, cloud) -> str:
    lane_ids = _unique_lane_ids(jobs)
    bus = EventBus()
    md = MultiAgentDisplay([(lid, sub) for lid, (_p, sub) in zip(lane_ids, jobs)])

    results: dict[str, str] = {}
    md.start()
    try:
        with ThreadPoolExecutor(max_workers=cfg.max_parallel_agents) as ex:
            futures = {}
            for lid, (preset, sub) in zip(lane_ids, jobs):
                emit = bus.emitter_for(lid)
                futures[ex.submit(run_agent, preset, sub, registry, cfg, emit, cloud)] = lid

            while not all(f.done() for f in futures):
                for ev in bus.drain():
                    md.apply(ev)
                md.refresh()
                time.sleep(0.08)

            for ev in bus.drain():  # final flush
                md.apply(ev)
            md.refresh()

            for f, lid in futures.items():
                try:
                    results[lid] = f.result()
                except Exception as e:
                    results[lid] = f"[ERROR] {type(e).__name__}: {e}"
    finally:
        md.stop()

    return _synthesize(task, lane_ids, results, cfg, cloud)


def _synthesize(task, lane_ids, results, cfg, cloud) -> str:
    model = cfg.planner_model or cfg.effective_model(cloud=cloud)
    blocks = "\n\n".join(f"## {lid}\n{results.get(lid, '')}" for lid in lane_ids)
    system = (
        "You are cortex. Combine the specialist results below into ONE coherent final "
        "answer for the user. Integrate the information, don't just concatenate. Be concise. "
        "Respond in the same language the user's original task was written in."
    )
    user = f"Original task: {task}\n\nSpecialist results:\n{blocks}"
    try:
        return llm.complete_text(model, system, user, cfg).strip() or blocks
    except Exception:
        return blocks  # worst case: show the raw per-agent results


# ── run-log ────────────────────────────────────────────────────────────────────────────

def _save_run_log(task, model, mode, jobs, duration_s) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task": task,
        "model": model,
        "mode": mode,
        "agents": [p.name for p, _ in jobs],
        "subtasks": [s for _, s in jobs],
        "duration_s": round(duration_s, 2),
    }
    (RUNS_DIR / f"{ts}.json").write_text(
        json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ── public entry ───────────────────────────────────────────────────────────────────────

def orchestrate(task: str, cfg: Settings, cloud: bool = False, verbose: bool = False,
                force_single: bool = False) -> str:
    """Route a task: single generalist or parallel specialists. Returns final answer."""
    registry = ToolRegistry.default(cfg)
    model = cfg.effective_model(cloud=cloud)
    start = time.time()
    stats.bump_runs()
    mem_block = memory.as_prompt_block(cfg.memory_recall) if cfg.memory_enabled else ""

    if force_single or not cfg.orchestrator_enabled:
        jobs = [(presets.generalist(), task)]
    else:
        jobs = _plan(task, cfg, cloud)

    # ── single path: identical to the classic single-agent experience ────────────
    if len(jobs) == 1:
        preset, sub = jobs[0]
        display = AgentDisplay(task=task, verbose=verbose)
        display.print_task()
        display.start()
        try:
            result = run_agent(preset, sub, registry, cfg, display.handle_event,
                               cloud, memory_block=mem_block)
        finally:
            display.stop()
        _save_run_log(task, model, "single", jobs, time.time() - start)
        _remember(cfg, task, result)
        return result

    # ── parallel path ────────────────────────────────────────────────────────────
    print_info(f"Plan: {len(jobs)} agents in parallel → " +
               ", ".join(p.name for p, _ in jobs))
    answer = _run_parallel(task, jobs, registry, cfg, cloud)
    _save_run_log(task, model, "parallel", jobs, time.time() - start)
    print_result(answer, meta=f"{len(jobs)} agents · {time.time() - start:.1f}s")
    _remember(cfg, task, answer)
    return answer


def _remember(cfg: Settings, task: str, result: str) -> None:
    if cfg.memory_enabled and result and not result.startswith("[ERROR]"):
        memory.remember(task, result)
