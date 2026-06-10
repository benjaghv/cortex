"""Agent/orchestration unit tests — no LLM, no network."""

from __future__ import annotations

from cortex.agents import orchestrator, presets
from cortex.agents.runner import run_agent
from cortex.config import Settings
from cortex.events import Event, EventBus
from cortex.tools.registry import ToolRegistry


def test_registry_subset():
    reg = ToolRegistry.default(Settings.load())
    # Core tools are always registered (count grows as tools are added).
    for tool in ("filesystem", "shell", "python_exec", "web", "document", "pptx"):
        assert tool in reg, f"{tool} missing from registry"
    sub = reg.subset(["filesystem", "shell", "python_exec"])
    assert sorted(sub.names()) == ["filesystem", "python_exec", "shell"]
    assert len(sub.schemas()) == 3
    assert sub.executor("web") is None          # filtered out
    assert sub.executor("shell") is not None


def test_registry_subset_ignores_unknown():
    reg = ToolRegistry.default(Settings.load())
    sub = reg.subset(["filesystem", "does_not_exist"])
    assert sub.names() == ["filesystem"]


def test_presets_have_valid_tools():
    reg = ToolRegistry.default(Settings.load())
    for p in presets.all_presets().values():
        for tool in p.tools:
            assert tool in reg, f"{p.name} references unknown tool {tool}"


def test_looks_simple_heuristic():
    assert orchestrator._looks_simple("qué hora es")
    assert orchestrator._looks_simple("precio de AAPL")
    assert not orchestrator._looks_simple(
        "busca qué es Python y dame el precio de la accion de Apple"
    )


def test_is_conversational_questions():
    # Pure questions / greetings / opinions → answer directly.
    assert orchestrator._is_conversational("qué es la recursividad?")
    assert orchestrator._is_conversational("¿cómo funciona git rebase?")
    assert orchestrator._is_conversational("explícame qué es un closure en Python")
    assert orchestrator._is_conversational("hola, cómo estás")
    assert orchestrator._is_conversational("qué opinas de usar threads aquí?")
    assert orchestrator._is_conversational("what is a monad?")


def test_is_conversational_rejects_actions_and_tools():
    # Action verbs → must NOT be treated as a chat question.
    assert not orchestrator._is_conversational("créame un script en Python")
    assert not orchestrator._is_conversational("haz un commit con los cambios")
    assert not orchestrator._is_conversational("genera una presentación sobre cortex")
    # Live-data / file signals → needs a tool even phrased as a question.
    assert not orchestrator._is_conversational("qué precio tiene la acción de Apple?")
    assert not orchestrator._is_conversational("qué clima hace en Santiago?")
    assert not orchestrator._is_conversational("lee el archivo config.toml")
    # URL → researcher, not chat.
    assert not orchestrator._is_conversational("resume el contenido de example.com")


def test_is_conversational_regressions():
    """Real cases that wrongly hit the no-tools path and made the model hallucinate."""
    # Git/GitHub question phrased politely → must use the git tool, not chat.
    assert not orchestrator._is_conversational("puedes chequear los ultimos cambios subidos a github?")
    assert not orchestrator._is_conversational("¿cuál es el último commit del repositorio?")
    # "que es" must only count as a starter at the START, not mid-sentence.
    assert not orchestrator._is_conversational("pero me refiero al repo que estoy que es de clinioapp")
    # A genuine conceptual question with the same words at the start still works.
    assert orchestrator._is_conversational("¿qué es un repositorio bare en git?") is False  # 'git'+'repositorio' signal
    assert orchestrator._is_conversational("qué es la herencia en POO")


def test_eventbus_emitter_stamps_agent():
    bus = EventBus()
    emit = bus.emitter_for("coder")
    emit(Event(agent="ignored", kind="thinking"))
    drained = bus.drain()
    assert len(drained) == 1
    assert drained[0].agent == "coder"


def test_run_agent_unknown_tool_forces_answer(monkeypatch):
    """If the model calls a tool outside the agent's subset, the runner must not crash."""
    cfg = Settings.load()
    reg = ToolRegistry.default(cfg)

    # Fake litellm responses: first a call to an out-of-scope tool, then a text answer.
    class _Fn:
        def __init__(self, name, args):
            self.name, self.arguments = name, args

    class _TC:
        def __init__(self, name, args):
            self.id, self.function = "tc1", _Fn(name, args)

    class _Msg:
        def __init__(self, content, tool_calls):
            self.content, self.tool_calls = content, tool_calls

    class _Resp:
        def __init__(self, msg):
            self.choices = [type("C", (), {"message": msg})()]

    calls = {"n": 0}

    def fake_with_tools(model, messages, schemas, cfg):
        calls["n"] += 1
        # coder has no 'web' tool → out of scope
        return _Resp(_Msg(None, [_TC("web", '{"url":"x"}')]))

    def fake_no_tools(model, messages, cfg):
        return _Resp(_Msg("Respuesta final", None))

    monkeypatch.setattr("cortex.agents.runner.llm.complete_with_tools", fake_with_tools)
    monkeypatch.setattr("cortex.agents.runner.llm.complete_no_tools", fake_no_tools)

    events: list[Event] = []
    coder = presets.get_preset("coder")
    result = run_agent(coder, "haz algo", reg, cfg, events.append)

    assert result == "Respuesta final"
    kinds = [e.kind for e in events]
    assert "tool_call" in kinds and "finished" in kinds
