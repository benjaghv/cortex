"""
cortex.events
─────────────
Event bus decoupling the agent loop from the display.

The ReAct loop never touches the console. It calls ``emit(Event(...))``. In
single-agent mode the emitter feeds AgentDisplay directly; in parallel mode many
worker threads emit into a thread-safe queue that one render thread drains.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

EventKind = Literal[
    "started",      # agent began
    "thinking",     # before an LLM call
    "tool_call",    # tool about to run        (tool, args)
    "tool_result",  # tool returned            (tool, result, ok)
    "finished",     # agent produced an answer (result)
    "error",        # agent-level failure      (result)
]


@dataclass
class Event:
    agent: str
    kind: EventKind
    tool: str | None = None
    args: dict | None = None
    result: str | None = None
    ok: bool = True
    ts: float = field(default_factory=time.time)


# An emitter is just a callable taking an Event. This lets the runner stay
# agnostic about whether it feeds a live display, a queue, or a test spy.
Emitter = Callable[[Event], Any]


class EventBus:
    """Thread-safe fan-in: workers ``emit``; the render loop ``drain``s."""

    def __init__(self) -> None:
        self._q: "queue.Queue[Event]" = queue.Queue()

    def emit(self, ev: Event) -> None:
        self._q.put(ev)

    def emitter_for(self, agent: str) -> Emitter:
        """Return an emitter that stamps every event with this agent's name."""
        def _emit(ev: Event) -> None:
            ev.agent = agent
            self._q.put(ev)
        return _emit

    def drain(self) -> list[Event]:
        """Non-blocking: pull everything currently queued."""
        out: list[Event] = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                break
        return out
