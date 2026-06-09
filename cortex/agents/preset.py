"""
cortex.agents.preset
────────────────────
An AgentPreset = a named role: a focused system prompt + a tool subset.
The orchestrator's planner routes subtasks to presets by name/description.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentPreset:
    name: str
    description: str            # one line — the planner reads this to route work
    system_prompt: str          # role-specific, built from prompt_base
    tools: tuple[str, ...]      # tool names this agent may use
    model: str | None = None    # optional per-agent model override
