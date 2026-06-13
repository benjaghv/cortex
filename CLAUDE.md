# CLAUDE.md — cortex project context

Local AI agent CLI powered by Ollama. Multi-agent orchestration, ReAct loop, streams every
tool call live. Zero API cost by default; optional cloud providers via litellm.

## Architecture

```
cortex/
  cli.py         → typer entrypoints: run, chat, voice, connect, accounts, disconnect, setup, agents,
                   models, history, stats, memory, config, version. `setup` checks/installs tool deps.
  agent.py       → compat shim: run() → dry-run preview OR orchestrate()
  events.py      → Event dataclass + EventBus (thread-safe queue). Decouples loop ↔ display.
  display.py     → ALL terminal visuals (Rich). AgentDisplay (single) + MultiAgentDisplay (parallel).
                   print_models_table() shows LOCAL (Ollama) + CLOUD sections.
  config.py      → pydantic-settings, loads ~/.cortex/config.toml. Env prefix: CORTEX_
  stats.py       → ~/.cortex/stats.json: cumulative tokens + runs. Savings vs cloud reference price.
  memory.py      → ~/.cortex/memory.jsonl: one entry per completed task (task → result summary).
  voice.py       → speech-to-text DICTATION (not an agent tool). Lazy SpeechRecognition+PyAudio.
                   Powers `cortex voice` + the /voice chat command: speak → transcript → run as task.

  integrations/
    google_auth.py → shared Google OAuth (BYO GCP client_secret). connect()/get_credentials()/
                     list_accounts()/set_active(). Tokens in ~/.cortex/credentials/google/<email>.json,
                     active.txt pointer. Lazy google-* imports. Persistent + switchable accounts.
    microsoft_auth.py → shared Microsoft Graph OAuth (BYO Azure app, microsoft_client_id). MSAL
                     device-code flow: connect(on_message)/get_token()/graph_get()/graph_post().
                     Token cache per ~/.cortex/credentials/microsoft/<email>.json + active.txt. Lazy msal.

  agents/
    preset.py       → AgentPreset dataclass (name, description, system_prompt, tools, model?)
    presets.py      → builtin presets: coder, researcher, data, generalist
    prompt_base.py  → shared prompt scaffolding (OS/paths/rules) + build_system_prompt()
    llm.py          → litellm wrappers: complete_with_tools / no_tools / json / text
                      _normalize_model(): maps user model strings to litellm format
                      _api_base() / _api_key(): routes Ollama local, Ollama Cloud, Kimi, Qwen, GLM
    runner.py       → run_agent(preset, task, registry, cfg, emit): ONE ReAct loop, emits Events
    orchestrator.py → orchestrate(): heuristic → planner → single OR parallel → synthesis

  tools/
    registry.py      → ToolRegistry: name→(SCHEMA, executor); .default(cfg) / .subset(names)
    filesystem.py    → read/write/mkdir/list/search local files (expands %VARS%/~, OneDrive-aware)
    shell.py         → subprocess with allowlist + blocked patterns + timeout
    web.py           → httpx GET, HTML stripped
    search.py        → DuckDuckGo web search (no key), filters ads
    stock.py         → Yahoo Finance real-time quotes (no key)
    weather.py       → Open-Meteo current + forecast (no key), geocodes city
    datetime_tool.py → current local date/time
    python_exec.py   → run Python snippet in subprocess, capture stdout (timeout)
    git_tool.py      → git ops with allowlist + blocked destructive patterns + retry
    browser.py       → Playwright headless Chromium (JS sites, job boards, SPAs)
    document.py      → create .docx (python-docx) / .txt from markdown-ish content
    pdf.py           → create .pdf (reportlab Platypus): markdown → headings/bullets/wrap/page-breaks
    pptx.py          → create .pptx (python-pptx) from a list of slide specs, 4 themes
    gmail.py         → Gmail (search/read/send/draft/trash) via integrations.google_auth; settings=cfg like shell
    outlook.py       → Outlook/M365 email (search/read/send/draft/trash) via integrations.microsoft_auth (Graph REST)

tests/
    test_tools.py    → tool logic only, no LLM/network
    test_agents.py   → registry subset, preset validity, planner fallback, eventbus
```

## Multi-agent orchestration

`cortex run "task"` → `orchestrator.orchestrate()`:
0. **Direct answer** (`_is_conversational`): pure questions/greetings/opinions with no action
   verb or live-data signal → `_answer_directly()`: ONE `complete_text` call, no agent loop,
   no tools. Gated by `cfg.direct_answer_enabled`. Conservative heuristic — when unsure it
   falls through to the agent flow (= old behavior), so a miss only costs latency.
1. **Heuristic** (`_looks_simple`): short/single-clause tasks skip the planner → single.
2. **Planner**: one JSON-mode LLM call lists presets, returns `{mode, subtasks:[{agent,task}]}`.
   Any failure/invalid JSON → fallback to single generalist (worst case = old behavior).
3. **single** → `run_agent(generalist)` driving `AgentDisplay` (one lane, identical to simple UX).
4. **parallel** → `ThreadPoolExecutor` runs each subtask's preset concurrently. Workers emit
   Events into an `EventBus`; the main thread pumps `MultiAgentDisplay.apply/refresh` (rich.Live,
   one lane per agent). Then a **synthesis** call merges results.

Concurrency = **threads** (litellm/httpx are sync but network-bound → release GIL).
`--single` / `cfg.orchestrator_enabled=False` forces single path.
`cfg.max_parallel_agents` caps concurrency (relevant on single-GPU Ollama setups).

Display: clean (default) = live spinner/lane table, hides retries; verbose (`-v`) = every step.
Loop guard: same tool call ≥2x → force_answer via complete_no_tools (temp 0, nudge to answer).
Unknown tool → inject error + force_answer.

## Key design decisions

1. **LiteLLM** is the LLM interface — model swappable via config string.
   `_normalize_model()` in `llm.py` maps user strings:
   - `ollama-cloud/<name>` → `ollama_chat/<name>` (Ollama Cloud uses Ollama protocol)
   - `moonshot-*/qwen-*/glm-*` → `openai/<name>` (OpenAI-compat endpoint)
   - `ollama/*` → unchanged
2. **Tool schemas** = OpenAI function-calling format. Each tool exports `SCHEMA` dict + `execute(**args) -> str`.
3. **ToolRegistry** in `tools/registry.py` is the single source of truth. Agents get `.subset(names)`.
4. **AgentDisplay / MultiAgentDisplay** in `display.py` own all output. Never `print()` directly.
5. **ReAct loop** in `runner.py`: `messages` grows each iteration. Tool results appended as
   `{"role": "tool", "tool_call_id": ..., "content": ...}`. Exits when model returns no tool_calls.
6. **Safety**: shell allowlist + blocked substrings + timeout. `--dry-run` shows planned calls.
7. Error results are strings prefixed `[ERROR]`/`[BLOCKED]`/`[EXIT n]`/`[TIMEOUT]`.
8. **Language**: UI is English. Agent responses match the user's input language (rule in prompt_base.py).

## Adding a new tool

1. Create `cortex/tools/newtool.py` with `SCHEMA` dict and `execute(**args) -> str`.
2. Register in `cortex/tools/registry.py` inside `_default_entries` — one line.
3. Add `("newtool", None): ("VERB", "icon", "style")` to `display.py` `_VERBS`.
   Unmapped tools fall back to ANALYZE.
4. Add to a preset's `tools` tuple in `cortex/agents/presets.py` (or create a new preset).
5. If it needs config (keys/urls), add the field to `config.py` `Settings`.
6. Dependencies: **pure-Python** deps go in `pyproject.toml` `[project] dependencies` (core, so every
   device gets them via `pip install -e .`). Only deps needing a **system lib/binary** (pyaudio→PortAudio,
   playwright→browser) go in `[project.optional-dependencies]` AND in `cli.py` `_TOOL_DEPS` so
   `cortex setup` can check/install them. Import such deps **lazily** inside `execute()` and return a
   clear `[ERROR]` if missing (see `browser.py`, `gmail.py`, `voice.py`).

## Adding a new agent preset

1. In `cortex/agents/presets.py`, call `_register(_make(...))` with name, description,
   role_intro, and a `tools` tuple referencing registry names.
2. Optionally set `model=` to override the default model for that preset.
3. The planner auto-discovers it from `presets.all_presets()` — no other changes needed.

## Cloud provider routing (`agents/llm.py`)

```
model string            → litellm model        api_base
─────────────────────────────────────────────────────────
ollama/<name>           → unchanged            cfg.ollama_base_url
ollama-cloud/<name>     → ollama_chat/<name>   cfg.ollama_cloud_base_url
moonshot-*/kimi-*       → openai/<name>        cfg.kimi_api_base
qwen-*                  → openai/<name>        cfg.qwen_api_base
glm-*                   → openai/<name>        cfg.glm_api_base
```

Keys read from `~/.cortex/config.toml` fields: `ollama_cloud_api_key`, `kimi_api_key`,
`qwen_api_key`, `glm_api_key`. Env var fallbacks: `OLLAMA_API_KEY`, `KIMI_API_KEY`, etc.

## Display conventions

- Tool start: `display.tool_call(name, args)` — step number + verb + args.
- Tool result: `display.tool_result(name, result, success=bool)`.
- Final answer: `display.final_answer(markdown)`.
- Models table: `print_models_table(models, cfg)` — LOCAL section (Ollama) + CLOUD section.
- Palette: agent=violet `#7C5CFF`, tool=cyan `#22D3EE`, output=green `#86EFAC`,
  error=red `#F87171`, meta=slate.

## Persistence

- **Stats**: `stats.py` → `~/.cortex/stats.json`. Every litellm call hits `stats.record_completion(resp)`.
  `cortex stats` shows savings vs reference cloud price (`cost_ref_*_per_1m` in config).
- **Memory**: `memory.py` → `~/.cortex/memory.jsonl`. `orchestrate()` saves a summary after each run
  and injects the last `memory_recall` entries into the agent's system prompt. `cortex memory [--clear]`.
- **Run logs**: `~/.cortex/runs/*.json` — per-run JSON with task, model, steps, duration.

## Config

`~/.cortex/config.toml` — outside the project, never committed. Created by `cortex config --init`.
`config.example.toml` in the repo shows all available fields with comments (safe to commit).
Env override prefix: `CORTEX_`.

## Running locally

```bash
pip install -e ".[dev]"
ollama serve                    # separate terminal
ollama pull qwen2.5-coder:7b
cortex config --init
cortex run "test task"
pytest -v
```

## Roadmap

- `cortex run --json` — machine-readable step stream.
- Per-tool confirmation prompt for write/shell (interactive safety).
- MCP server support via litellm.
- Streaming token output during thinking phase.
- Semantic memory with `nomic-embed-text` (vector search replacing jsonl recall).
- DAG `depends_on` between parallel subtasks (sequential + parallel mixed).
- `analyst` preset: deepseek-r1:8b + python_exec + filesystem.
