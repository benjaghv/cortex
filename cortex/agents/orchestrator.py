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
import re
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
        "- Tasks that involve fetching/reading a URL or website → assign to 'researcher' ONLY "
        "(it has the web tool). NEVER assign web/URL tasks to 'coder' (no web tool).\n"
        "- If the task is a single action (fetch one URL, ask one question, get one thing), "
        "use 'single' — do NOT invent parallel subtasks for a single-step task.\n"
        "- Prefer 'single' unless parallelism clearly helps (e.g. fetch stock prices AND "
        "fetch weather at the same time, with no dependency between them).\n"
        "Available agents:\n"
        f"{lines}\n"
        "Return STRICT JSON only, no prose:\n"
        '{"mode":"single"|"parallel","subtasks":[{"agent":"<name>","task":"<text>"}]}\n'
        "For 'single', subtasks has exactly one item using the 'generalist' agent."
    )


_URL_PATTERNS = (".cl", ".com", ".org", ".net", ".io", ".dev", ".ai", "http://", "https://", "www.")
_WEB_VERBS = ("visita", "visítame", "dirígete", "dirigete", "abre", "analiza", "resume",
              "lee la página", "lee el sitio", "fetch", "go to", "visit", "open", "analyze")

# Git workflows are always sequential → single agent, never split
_GIT_KEYWORDS = ("commit", "push", "pull", "git ", "branch", "checkout",
                 "merge", "rebase", "stash", "diff", "status", "add ")

# Document creation is always a single-agent task
_DOC_KEYWORDS = ("word", ".docx", "documento", "document", "crea un word",
                 "crea el word", "genérame un word", "generame un word",
                 "escríbeme un", "escribe un documento", "write a doc")

# Email workflows are sequential (read → compose → send) → single agent
_EMAIL_KEYWORDS = ("correo", "email", "gmail", "outlook", "inbox", "bandeja", "mail")


def _looks_simple(task: str) -> bool:
    """Cheap pre-check: short/single-clause tasks and single-URL tasks skip the planner."""
    t = task.strip().lower()

    # Short tasks are always simple
    if len(t.split()) <= 6:
        return True

    # Git workflows are always sequential — never parallelize
    if any(kw in t for kw in _GIT_KEYWORDS):
        return True

    # Document creation is always single
    if any(kw in t for kw in _DOC_KEYWORDS):
        return True

    # Email workflows are always single (sequential, with a confirm gate)
    if any(kw in t for kw in _EMAIL_KEYWORDS):
        return True

    # Tasks with a URL/domain → always single (researcher handles web, no need to split)
    if any(pat in t for pat in _URL_PATTERNS):
        return True

    # Multiple independent data requests → allow planner to parallelize
    connectors = (" y ", " and ", "; ", ", luego", ", después", " then ")
    return not any(c in t for c in connectors)


# ── intent: question vs action ─────────────────────────────────────────────────────────
# Goal: a pure question/greeting/explanation is answered DIRECTLY (one LLM call, no agent
# loop, no tools). Anything that wants something DONE, or needs live data, falls through to
# the normal agent/tool flow. Conservative by design: when unsure → normal flow (= old
# behavior), so a false negative just costs a bit of latency, never a wrong answer.

# Verbs that mean "do/make something" → use agents/tools, not a chat reply.
_ACTION_VERBS = frozenset((
    "crea", "créa", "crear", "créame", "creame", "haz", "hazme", "hacer",
    "escribe", "escríbeme", "escribeme", "genera", "genérame", "generame",
    "guarda", "guárdame", "guardame", "busca", "búscame", "buscame", "descarga",
    "descárgame", "abre", "ábreme", "lee", "léeme", "leeme", "corre", "ejecuta",
    "instala", "muéstrame", "muestrame", "dame", "consigue", "actualiza",
    "modifica", "edita", "elimina", "borra", "renombra", "mueve", "copia",
    "clona", "commit", "commitea", "pushea", "push", "calcula", "convierte",
    "envía", "envia", "envíame", "enviame", "manda", "mándame", "mandame",
    "responde", "respóndele", "respondele", "reenvía", "reenvia", "send", "reply",
    "traduce", "resume", "resúmeme", "analiza", "analízame", "revisa", "revisar",
    "compila", "chequea", "chequear", "checa", "checar", "verifica", "verificar",
    "comprueba", "comprobar", "mira", "míra", "fíjate", "fijate", "muestra",
    "create", "make", "write", "generate", "save", "search", "download",
    "open", "read", "run", "build", "fetch", "install", "update", "delete",
    "remove", "calculate", "translate", "summarize", "analyze", "review", "check",
))

# Phrasings that need a tool even when written as a question.
_TOOL_SIGNALS = (
    "clima", "pronóstico", "pronostico", "temperatura", "weather",
    "precio", "price", "acción de", "acciones de", "stock", "cotización", "cotizacion",
    "bitcoin", "crypto", "cripto", "dólar", "dolar", "euro",
    "qué hora", "que hora", "qué día", "que dia", "fecha de hoy",
    "what time", "today's date", "what's the date",
    "archivo", "carpeta", "documento", "presentación", "presentacion",
    ".docx", ".pptx", "file", "folder",
    # version control — these questions need the git tool, never a chat reply
    "github", "repositorio", "commit", "git status", "git log",
    "cambios subidos", "últimos cambios", "ultimos cambios", "rama main",
    # email — needs the gmail/outlook tool
    "correo", "correos", "email", "emails", "gmail", "outlook", "inbox", "bandeja",
    "no leídos", "no leidos", "unread",
)

# Conceptual openers → answer from own knowledge.
_QUESTION_STARTERS = (
    "qué es", "que es", "qué son", "que son", "cuál es", "cual es",
    "cómo funciona", "como funciona", "por qué", "por que", "porqué", "porque",
    "explica", "explícame", "explicame", "define", "definición de", "definicion de",
    "diferencia entre", "qué significa", "que significa", "para qué sirve",
    "para que sirve", "qué opinas", "que opinas", "tiene sentido", "me recomiendas",
    "what is", "what are", "how does", "why ", "explain", "tell me about",
    "what's the difference", "do you think", "should i",
)

_GREETINGS = (
    "hola", "buenas", "hey", "hi ", "hello", "qué tal", "que tal",
    "cómo estás", "como estas", "buenos días", "buenos dias", "buenas tardes",
    "buenas noches", "gracias", "thanks", "thank you",
)


def _is_conversational(task: str) -> bool:
    """True only when confident the task is a pure question/greeting needing NO tools.

    Conservative: the direct path has NO tools, so a wrong "yes" makes the model
    hallucinate (e.g. answering a git question from imagination). A wrong "no" only
    costs latency — the agent flow answers conceptual questions directly anyway.
    So we fire ONLY on positive signals matched at the START of the message; there is
    deliberately no generic "ends with ?" fallback (it caught tool-needing requests
    like "¿puedes chequear los cambios en github?").
    """
    t = task.strip().lower().lstrip("¿¡ ").strip()
    if not t:
        return False

    # Live-data / file / version-control / URL signals → needs a tool, not a chat reply.
    if any(sig in t for sig in _TOOL_SIGNALS):
        return False
    if any(pat in t for pat in _URL_PATTERNS):
        return False

    # An action/inspection verb anywhere (whole-word match) → the user wants something done.
    words = set(re.findall(r"[a-záéíóúñü]+", t))
    if words & _ACTION_VERBS:
        return False

    # Positive signals must appear at the START (prefix), never mid-sentence —
    # otherwise "…que es de clinioapp" would match the "que es" starter.
    if any(t.startswith(g) for g in _GREETINGS):
        return True
    if any(t.startswith(s) for s in _QUESTION_STARTERS):
        return True
    return False


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

    # Filter out pure-error results so they don't pollute the synthesis
    good = {lid: r for lid, r in results.items() if not r.startswith("[ERROR]") and not r.startswith("[BLOCKED]")}
    use = good if good else results  # fall back to all if everything failed

    blocks = "\n\n".join(f"## {lid}\n{use.get(lid, '')}" for lid in lane_ids if lid in use)
    system = (
        "You are cortex. Combine the specialist results below into ONE coherent final "
        "answer for the user. Integrate the information, don't just concatenate. Be concise. "
        "NEVER mention agent names or internal errors in your answer. "
        "Respond in the same language the user's original task was written in."
    )
    user = f"Original task: {task}\n\nSpecialist results:\n{blocks}"
    try:
        return llm.complete_text(model, system, user, cfg).strip() or blocks
    except Exception:
        return blocks


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


# ── direct-answer path (no agent loop, no tools) ─────────────────────────────────────────

def _answer_directly(task: str, cfg: Settings, cloud: bool, verbose: bool,
                     mem_block: str, session_context: str) -> str:
    """Answer a conversational question with ONE LLM call — no ReAct loop, no tools."""
    model = cfg.effective_model(cloud=cloud)
    system = (
        "You are cortex, a helpful and friendly AI assistant. The user is chatting or "
        "asking a question — answer it directly, clearly and concisely from your own "
        "knowledge. You are NOT using any tools this turn. "
        "If the answer would genuinely require live data (weather, prices, a local file, "
        "a URL), say briefly what you'd need to fetch instead of inventing it. "
        "Answer in readable markdown, in the SAME language the user wrote in."
    )
    if getattr(cfg, "freestyle", False):
        from cortex.agents.prompt_base import FREESTYLE_RULES
        system += FREESTYLE_RULES
    if mem_block:
        system += "\n\n" + mem_block
    if session_context:
        system += "\n\n" + session_context

    display = AgentDisplay(task=task, verbose=verbose)
    display.print_task()
    display.start()
    display.on_thinking()
    try:
        answer = llm.humanize(llm.complete_text(model, system, task, cfg).strip())
    finally:
        display.stop()

    answer = answer or "(no response)"
    print_result(answer, meta="direct answer · no tools")
    return answer


# ── public entry ───────────────────────────────────────────────────────────────────────

def orchestrate(task: str, cfg: Settings, cloud: bool = False, verbose: bool = False,
                force_single: bool = False, session_context: str = "") -> str:
    """Route a task: single generalist or parallel specialists. Returns final answer."""
    registry = ToolRegistry.default(cfg)
    model = cfg.effective_model(cloud=cloud)
    start = time.time()
    stats.bump_runs()
    mem_block = memory.as_prompt_block(cfg.memory_recall) if cfg.memory_enabled else ""

    # ── direct-answer path: pure questions/greetings skip the agent entirely ──────
    if cfg.direct_answer_enabled and _is_conversational(task):
        answer = _answer_directly(task, cfg, cloud, verbose, mem_block, session_context)
        _save_run_log(task, model, "direct", [(presets.generalist(), task)], time.time() - start)
        _remember(cfg, task, answer)
        return answer

    if force_single or not cfg.orchestrator_enabled:
        jobs = [(presets.generalist(), task)]
    else:
        jobs = _plan(task, cfg, cloud)

    # ── single path: identical to the classic single-agent experience ────────────
    if len(jobs) == 1:
        preset, sub = jobs[0]
        display = AgentDisplay(task=task, verbose=verbose)
        display.agent_name = preset.name
        display.print_task()
        display.start()
        try:
            result = run_agent(preset, sub, registry, cfg, display.handle_event,
                               cloud, memory_block=mem_block, session_context=session_context)
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
