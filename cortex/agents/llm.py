"""
cortex.agents.llm
─────────────────
Thin wrappers around litellm.completion used by the runner (tool loop) and the
orchestrator (planner + synthesis). One place that knows how to talk to the model
and how to point Ollama at its base URL.
"""

from __future__ import annotations

import json
from typing import Any

from cortex import stats
from cortex.config import Settings


def _is_ollama(model: str) -> bool:
    return "ollama" in model


def _is_cloud(model: str) -> bool:
    """True for any non-Ollama provider we route to a custom api_base."""
    if _is_ollama(model):
        return False
    return any(tok in model.lower() for tok in ("moonshot", "kimi", "glm", "qwen-turbo", "qwen-plus", "qwen-max"))


def _normalize_model(model: str) -> str:
    """Map user-facing model names to litellm model strings.

    ollama-cloud/<name>  → ollama_chat/<name>  (api.ollama.com uses Ollama protocol)
    moonshot/glm/qwen-*  → openai/<name>       (OpenAI-compat endpoint)
    ollama/*             → unchanged            (litellm knows ollama/ prefix)
    """
    if model.startswith("ollama-cloud/"):
        return f"ollama_chat/{model[len('ollama-cloud/'):]}"
    if model.startswith("openai/") or model.startswith("ollama/") or model.startswith("ollama_chat/"):
        return model
    if _is_cloud(model):
        return f"openai/{model}"
    return model


def _api_base(model: str, cfg: Settings) -> "str | None":
    # Ollama Cloud: model written as "ollama-cloud/<name>"
    if model.startswith("ollama-cloud/"):
        return cfg.ollama_cloud_base_url
    if _is_ollama(model):
        return cfg.ollama_base_url
    if "moonshot" in model or "kimi" in model.lower():
        return cfg.kimi_api_base
    if "qwen" in model:
        return cfg.qwen_api_base
    if "glm" in model:
        return cfg.glm_api_base
    return None


def _api_key(model: str, cfg: Settings) -> "str | None":
    """Return the right API key for OpenAI-compat providers."""
    import os
    if model.startswith("ollama-cloud/"):
        return cfg.ollama_cloud_api_key or os.getenv("OLLAMA_API_KEY")
    if _is_ollama(model):
        return None  # local, no key needed
    if "moonshot" in model or "kimi" in model.lower():
        return cfg.kimi_api_key or os.getenv("KIMI_API_KEY")
    if "qwen" in model:
        return cfg.qwen_api_key or os.getenv("DASHSCOPE_API_KEY")
    if "glm" in model:
        return cfg.glm_api_key or os.getenv("GLM_API_KEY")
    return cfg.openai_api_key or os.getenv("OPENAI_API_KEY")


def complete_with_tools(model: str, messages: list[dict], schemas: list[dict], cfg: Settings):
    """Normal ReAct step: model may call tools."""
    import litellm

    m = _normalize_model(model)
    litellm.drop_params = True
    resp = litellm.completion(
        model=m,
        messages=messages,
        tools=schemas,
        tool_choice="auto",
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        api_base=_api_base(model, cfg),
        api_key=_api_key(model, cfg),
    )
    stats.record_completion(resp)
    return resp


def complete_no_tools(model: str, messages: list[dict], cfg: Settings):
    """Force a text-only answer from data already gathered — no tools, temp 0."""
    import litellm

    m = _normalize_model(model)
    litellm.drop_params = True
    nudged = messages + [{
        "role": "system",
        "content": (
            "Answer the user's original question NOW using the tool results already in "
            "this conversation. Do NOT apologize, do NOT say you can't access data — the "
            "data is above. Respond concisely and directly, in the same language the user used."
        ),
    }]
    resp = litellm.completion(
        model=m,
        messages=nudged,
        max_tokens=cfg.max_tokens,
        temperature=0.0,
        api_base=_api_base(model, cfg),
        api_key=_api_key(model, cfg),
    )
    stats.record_completion(resp)
    return resp


def complete_json(model: str, system: str, user: str, cfg: Settings) -> str:
    """Single planner call asking for a JSON object. Returns raw content string."""
    import litellm

    m = _normalize_model(model)
    litellm.drop_params = True
    resp = litellm.completion(
        model=m,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=cfg.max_tokens,
        response_format={"type": "json_object"},
        api_base=_api_base(model, cfg),
        api_key=_api_key(model, cfg),
    )
    stats.record_completion(resp)
    return resp.choices[0].message.content or "{}"


def complete_text(model: str, system: str, user: str, cfg: Settings) -> str:
    """Single text call (synthesis). Returns content string."""
    import litellm

    m = _normalize_model(model)
    litellm.drop_params = True
    resp = litellm.completion(
        model=m,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.2,
        max_tokens=cfg.max_tokens,
        api_base=_api_base(model, cfg),
        api_key=_api_key(model, cfg),
    )
    stats.record_completion(resp)
    return resp.choices[0].message.content or ""


def humanize(text: str) -> str:
    """If the model returned JSON instead of markdown, convert it to readable text."""
    stripped = text.strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return text
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return text

    lines: list[str] = []

    def _render(obj: Any, indent: int = 0) -> None:
        prefix = "  " * indent
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"{prefix}**{k}:**")
                    _render(v, indent + 1)
                else:
                    lines.append(f"{prefix}- **{k}:** {v}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("title") or item.get("label")
                    rest = {k: v for k, v in item.items() if k not in ("name", "title", "label")}
                    if name and rest:
                        rest_str = "  ·  ".join(f"{k}: {v}" for k, v in rest.items())
                        lines.append(f"{prefix}- **{name}** — {rest_str}")
                    elif name:
                        lines.append(f"{prefix}- {name}")
                    else:
                        _render(item, indent)
                else:
                    lines.append(f"{prefix}- {item}")
        else:
            lines.append(f"{prefix}{obj}")

    _render(data)
    return "\n".join(lines)
