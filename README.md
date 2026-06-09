```
 ___ ___  ___ _____ _____  __
/ __/ _ \| _ \_   _| __\ \/ /
| (_| (_) |   / | | | _| >  <
 \___\___/|_|_\ |_| |___/_/\_\
```

# cortex

**Local AI agents with tools, in your terminal — powered by Ollama. Zero API cost, full control.**

Ask a question. Cortex figures out which specialized agents to run, routes them in parallel if it helps, streams every step live, and gives you one clean answer.

---

## How it works

```
cortex run "get AAPL price and today's weather in Santiago"
         │
         ▼
   ┌─────────────┐
   │ Orchestrator │  ← one LLM call decides: single or parallel?
   └──────┬──────┘
          │  parallel (independent subtasks)
    ┌─────┴──────┐
    ▼            ▼
 [data agent]  [data agent]          ← each gets only its relevant tools
 stock: AAPL   weather: Santiago     ← run concurrently via threads
    │            │
    └─────┬──────┘
          ▼
   ┌─────────────┐
   │  Synthesizer │  ← combines results into one final answer
   └─────────────┘
```

**Single task** → one generalist agent handles it (same as a basic AI assistant).

**Complex task** → a fast planner LLM call decomposes it into subtasks, assigns specialist agents, runs them in parallel, then merges the results. Falls back to single if planning fails — worst case is always the simple path.

---

## Requirements

- **Python 3.11+**
- **[Ollama](https://ollama.com)** installed and running

---

## Installation

```bash
# 1. Clone the repo
git clone https://github.com/benjaghv/cortex
cd cortex

# 2. Create a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install cortex
pip install -e ".[dev]"
```

---

## Setup

### 1. Start Ollama and pull a model

```bash
ollama serve                          # keep this running in a separate terminal
ollama pull qwen2.5-coder:7b          # recommended default
ollama pull qwen2.5-coder:1.5b        # fast planner (optional but useful)
```

### 2. Initialize config

```bash
cortex config --init
```

This creates `~/.cortex/config.toml` with sensible defaults. The config lives **outside the project** — it may contain API keys and should never be committed to git.

**Default config (`~/.cortex/config.toml`):**

```toml
model = "ollama/qwen2.5-coder:7b"
planner_model = "ollama/qwen2.5-coder:1.5b"
ollama_base_url = "http://localhost:11434"
max_tokens = 4096
temperature = 0.1
max_iterations = 20
truncate_output = 3000
```

### 3. Verify setup

```bash
cortex run "what time is it?"
```

You should see the task banner, a `datetime` tool call, and a response. Setup is complete.

---

## First steps

### One-shot task

```bash
cortex run "what's the weather in Tokyo?"
cortex run "read my README.md and summarize it"
cortex run "get AAPL and NVDA stock prices"
cortex run "search the web for the latest Python release notes"
```

### Interactive chat session

```bash
cortex chat
```

Inside the chat, type any task. Available slash commands:

| Command | Action |
|---|---|
| `/models` | list local and cloud models |
| `/model <name>` | switch model for this session |
| `/model <number>` | switch by number from the list |
| `/verbose` | toggle verbose mode (shows every step) |
| `/dry-run <task>` | plan without executing |
| `exit` | quit |

### Useful flags

```bash
cortex run --single "task"      # skip orchestration, use one generalist agent
cortex run --dry-run "task"     # show planned tool calls without executing
cortex run -v "task"            # verbose: show every step, args, and errors
cortex run -m ollama/qwen3:8b "task"  # override model for this run
```

---

## Commands reference

```
cortex run "task"        Run a task (auto-orchestrates agents)
cortex chat              Interactive multi-task session
cortex agents            List all agent presets and their tools
cortex models            List local Ollama models + configured cloud providers
cortex history           Show recent run history
cortex stats             Show tokens used and estimated cloud savings
cortex memory            Show remembered past tasks
cortex memory --clear    Clear all session memory
cortex config --init     Create default config file at ~/.cortex/config.toml
cortex version           Show version
```

---

## Agent presets

Cortex ships with 4 specialist agents. The orchestrator picks which ones to use based on your task.

| Agent | Tools | Best for |
|---|---|---|
| **generalist** | all tools | simple or ambiguous tasks — default fallback |
| **coder** | filesystem, shell, python_exec | reading/writing files, running scripts |
| **researcher** | search, web, filesystem | web search, URL fetching, summarizing docs |
| **data** | stock, weather, datetime, python_exec | live prices, weather, date math |

Each agent only sees its assigned tools — a researcher can't accidentally write files; a coder can't drift into web searches. The orchestrator assigns the right specialist to each part of your task automatically.

---

## Built-in tools

| Tool | What it does | Needs API key? |
|---|---|---|
| `filesystem` | Read, write, list, search, create folders | No |
| `shell` | Run allowed shell commands (git, dir, python…) | No |
| `web` | Fetch a URL and extract its text | No |
| `search` | DuckDuckGo web search | No |
| `stock` | Real-time stock and crypto quotes | No (Yahoo Finance) |
| `weather` | Current weather + forecast for any city | No (Open-Meteo) |
| `datetime` | Current local date and time | No |
| `python_exec` | Run a Python snippet, capture output | No |

---

## Project structure

```
cortex/
  cli.py              → CLI commands (run, chat, agents, models, history, stats, memory, config)
  agent.py            → Shim: delegates to orchestrate() or dry-run preview
  config.py           → Settings loaded from ~/.cortex/config.toml
  display.py          → All terminal output (Rich). AgentDisplay + MultiAgentDisplay
  events.py           → Thread-safe EventBus — decouples agent loop from display
  stats.py            → Token counting + cloud savings estimate
  memory.py           → Cross-session task memory

  agents/
    preset.py         → AgentPreset dataclass (name, description, system_prompt, tools, model?)
    presets.py        → Built-in presets: coder, researcher, data, generalist
    prompt_base.py    → Shared system-prompt scaffolding (OS, paths, rules)
    llm.py            → litellm wrappers for all LLM calls (local + cloud routing)
    runner.py         → run_agent(): one full ReAct loop, emits Events
    orchestrator.py   → orchestrate(): heuristic → planner → single/parallel → synthesis

  tools/
    registry.py       → ToolRegistry: name → (schema, executor)
    filesystem.py     → Local file operations
    shell.py          → Shell with allowlist + blocked patterns + timeout
    web.py            → HTTP fetch
    search.py         → DuckDuckGo
    stock.py          → Yahoo Finance
    weather.py        → Open-Meteo
    datetime_tool.py  → Current date/time
    python_exec.py    → Python subprocess

~/.cortex/             (auto-created, never committed to git)
  config.toml         → Your personal config and API keys
  stats.json          → Cumulative token usage
  memory.jsonl        → Past task memory
  runs/               → Per-run JSON logs
```

---

## Cloud providers (optional)

Cortex works 100% locally. Cloud providers are opt-in — add a key to `~/.cortex/config.toml` and the model becomes available via `-m`.

```bash
cortex models   # shows which providers are configured (● = active, ○ = no key)
```

### Ollama Cloud

Models hosted on `api.ollama.com` — same interface as local Ollama, cloud scale.

```toml
ollama_cloud_api_key = "your-key"   # from ollama.com → Settings → API Keys
```

```bash
cortex run -m "ollama-cloud/gemma3:4b" "your task"    # free tier
cortex run -m "ollama-cloud/kimi-k2.6" "your task"    # paid tier
```

### Kimi (Moonshot AI) — 128K context

```toml
kimi_api_key = "sk-..."   # from platform.moonshot.cn
```

```bash
cortex run -m moonshot-v1-128k "your task"
```

### Qwen (Alibaba Cloud) — 1M context

```toml
qwen_api_key = "sk-..."   # from dashscope.aliyuncs.com
```

```bash
cortex run -m qwen-plus "your task"
```

### GLM (Zhipu AI)

```toml
glm_api_key = "..."   # from open.bigmodel.cn
```

```bash
cortex run -m glm-4-plus "your task"
```

---

## Memory

After each run, a short summary is saved to `~/.cortex/memory.jsonl` and automatically injected into the next run's context. Cortex remembers what it did before.

```bash
cortex memory           # view recent remembered tasks
cortex memory --clear   # clear all memory
```

---

## Stats

```bash
cortex stats
```

Shows total tokens used and estimated savings versus cloud API pricing (configurable via `cost_ref_input_per_1m` and `cost_ref_output_per_1m` in config).

---

## Adding a new tool

1. Create `cortex/tools/mytool.py` with a `SCHEMA` dict and `execute(**args) -> str`.
2. Register it in `cortex/tools/registry.py` inside `_default_entries`.
3. Add a verb in `cortex/display.py` inside `_VERBS` (controls how it appears in the live display).
4. Add it to a preset's `tools` tuple in `cortex/agents/presets.py`.

---

## Config reference

```toml
# Model
model = "ollama/qwen2.5-coder:7b"         # default agent model
planner_model = "ollama/qwen2.5-coder:1.5b"  # model used only for task planning
ollama_base_url = "http://localhost:11434"

# Behavior
max_tokens = 4096
temperature = 0.1          # lower = more deterministic tool use
max_iterations = 20        # max tool-call steps per agent
truncate_output = 3000     # max chars of tool output shown to the model

# Orchestration
orchestrator_enabled = true
max_parallel_agents = 4

# Memory
memory_enabled = true
memory_recall = 5          # how many past tasks to inject per run

# Cloud savings estimate
cost_ref_input_per_1m = 2.50    # USD, GPT-4o class reference
cost_ref_output_per_1m = 10.00
```

---

## Running tests

```bash
pytest -v
```

Tests cover tool logic only — no LLM calls or network required.

---

## License

MIT
