"""
cortex.memory
─────────────
Lightweight persistent memory so cortex remembers past tasks across sessions.

Each completed task appends one entry (timestamp, task, short result) to
~/.cortex/memory.jsonl. The most recent entries are injected into the agent's
system prompt as "MEMORIA DE SESIONES ANTERIORES", giving continuity when you
re-enter — e.g. it knows what folder it just created or what you asked before.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from cortex.config import CONFIG_DIR

MEMORY_FILE = CONFIG_DIR / "memory.jsonl"
_MAX_RESULT = 280  # keep injected entries short


def remember(task: str, result: str) -> None:
    """Append one task→result entry. Best-effort; never breaks a run."""
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task": task.strip()[:300],
            "result": _short(result),
        }
        with MEMORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _short(text: str) -> str:
    t = " ".join((text or "").split())
    return t[:_MAX_RESULT] + ("…" if len(t) > _MAX_RESULT else "")


def recent(n: int = 5) -> list[dict]:
    """Last n entries, oldest→newest."""
    if not MEMORY_FILE.exists():
        return []
    try:
        lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict] = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def as_prompt_block(n: int = 5) -> str:
    """Format recent memory for injection into a system prompt. Empty if none."""
    items = recent(n)
    if not items:
        return ""
    lines = ["PAST SESSION MEMORY (recent context — use if relevant to the current task):"]
    for it in items:
        when = it.get("ts", "")[:10]
        lines.append(f"- [{when}] Tarea: {it.get('task','')} → {it.get('result','')}")
    return "\n".join(lines)


def clear() -> None:
    try:
        if MEMORY_FILE.exists():
            MEMORY_FILE.unlink()
    except Exception:
        pass
