"""
cortex.config
─────────────
Configuration loaded from ~/.cortex/config.toml
Sensible defaults so it works out of the box.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import toml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".cortex"
CONFIG_FILE = CONFIG_DIR / "config.toml"
RUNS_DIR = CONFIG_DIR / "runs"
CREDENTIALS_DIR = CONFIG_DIR / "credentials"


def _ensure_dirs() -> None:
    CONFIG_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)
    CREDENTIALS_DIR.mkdir(exist_ok=True)


def _load_toml() -> dict:
    if CONFIG_FILE.exists():
        return toml.load(CONFIG_FILE)
    return {}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CORTEX_", env_file=".env")

    # ── LLM ────────────────────────────────────────────────────────────────────
    model: str = Field(default="ollama/qwen2.5-coder:7b", description="LiteLLM model string")
    ollama_base_url: str = Field(default="http://localhost:11434")
    max_tokens: int = Field(default=4096)
    temperature: float = Field(default=0.1, description="Low temp = more deterministic tool use")
    max_iterations: int = Field(default=20, description="Max agent loop iterations")

    # ── Optional cloud fallback ─────────────────────────────────────────────────
    fallback_model: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)
    openai_api_key: Optional[str] = Field(default=None)

    # ── Ollama Cloud (api.ollama.com) ────────────────────────────────────────────
    ollama_cloud_api_key: Optional[str] = Field(
        default=None,
        description="API key for Ollama Cloud (api.ollama.com). Models with cloud icon in Ollama app."
    )
    ollama_cloud_base_url: str = Field(default="https://api.ollama.com")

    # ── Cloud providers (OpenAI-compatible) ──────────────────────────────────────
    # Kimi K2.6 (Moonshot AI) — best for long agent runs, 256K ctx, 1T MoE params
    kimi_api_key: Optional[str] = Field(default=None)
    kimi_api_base: str = Field(default="https://api.moonshot.cn/v1")
    kimi_model: str = Field(default="moonshot-v1-128k")

    # Qwen3.6 (Alibaba Cloud) — top agentic coding, 1M ctx, SWE-Bench leader
    qwen_api_key: Optional[str] = Field(default=None)
    qwen_api_base: str = Field(default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    qwen_model: str = Field(default="qwen-plus")

    # GLM-5.1 (Zhipu AI) — UI/frontend specialist, Code Arena Elo 1530
    glm_api_key: Optional[str] = Field(default=None)
    glm_api_base: str = Field(default="https://open.bigmodel.cn/api/paas/v4")
    glm_model: str = Field(default="glm-4-plus")

    # ── Shell tool safety ───────────────────────────────────────────────────────
    shell_allowed_commands: list[str] = Field(
        default=[
            "git", "ls", "dir", "find", "grep", "findstr", "cat", "type",
            "echo", "pwd", "cd", "curl", "python", "pip", "npm", "node", "docker",
            "mkdir", "md", "cp", "mv", "touch", "wc", "rm", "del", "copy", "move",
        ],
        description="Allowlist of base commands the shell tool can run",
    )
    shell_blocked_patterns: list[str] = Field(
        default=["rm -rf /", "shutdown", "reboot", "mkfs", "dd if=", "format ", "del /f"],
        description="Blocklist substrings — if matched, shell tool refuses",
    )
    shell_timeout_seconds: int = Field(default=30)

    # ── Orchestration (multi-agent) ──────────────────────────────────────────────
    orchestrator_enabled: bool = Field(
        default=True, description="Let `run` auto-split tasks across parallel agents"
    )
    max_parallel_agents: int = Field(default=4, description="Cap on concurrent agents")
    planner_model: Optional[str] = Field(
        default=None, description="Model for planning/synthesis; defaults to main model"
    )
    direct_answer_enabled: bool = Field(
        default=True,
        description="Answer pure questions/greetings directly (one LLM call, no agent loop or tools)",
    )
    voice_language: str = Field(
        default="es-ES",
        description="Speech-to-text language for /voice dictation (e.g. es-ES, en-US)",
    )

    # ── Savings estimate (vs cloud) ───────────────────────────────────────────────
    cost_ref_input_per_1m: float = Field(
        default=3.00, description="Reference cloud price USD per 1M input tokens (Claude Sonnet / GPT-4.1)"
    )
    cost_ref_output_per_1m: float = Field(
        default=15.00, description="Reference cloud price USD per 1M output tokens"
    )

    # ── Memory ────────────────────────────────────────────────────────────────────
    memory_enabled: bool = Field(default=True, description="Persist + recall past tasks")
    memory_recall: int = Field(default=5, description="How many past entries to inject")

    # ── Display ─────────────────────────────────────────────────────────────────
    show_thinking: bool = Field(default=True)
    truncate_output: int = Field(default=2000)

    # ── Integrations: Google / Gmail ─────────────────────────────────────────────
    google_client_secret_path: Optional[str] = Field(
        default=None,
        description="Path to your Google Cloud OAuth client_secret.json (BYO project). "
                    "If unset, cortex looks in ~/.cortex/credentials/google_client_secret.json",
    )
    gmail_scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.readonly",  # search / read
            "https://www.googleapis.com/auth/gmail.send",      # send / draft
            "https://www.googleapis.com/auth/gmail.modify",    # trash (recoverable)
        ],
        description="OAuth scopes for Gmail. Read + send + modify (trash). "
                    "Permanent delete is intentionally NOT granted.",
    )
    gmail_enabled: bool = Field(default=True, description="Expose the gmail tool to agents")

    @classmethod
    def load(cls) -> "Settings":
        _ensure_dirs()
        toml_data = _load_toml()
        flat: dict = {}
        for section, values in toml_data.items():
            if isinstance(values, dict):
                flat.update(values)
            else:
                flat[section] = values
        return cls(**{k: v for k, v in flat.items() if k in cls.model_fields})

    def save_default(self) -> None:
        if CONFIG_FILE.exists():
            return
        default = {
            "model": self.model,
            "ollama_base_url": self.ollama_base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "max_iterations": self.max_iterations,
            "show_thinking": self.show_thinking,
            "truncate_output": self.truncate_output,
        }
        CONFIG_FILE.write_text(toml.dumps(default), encoding="utf-8")

    def effective_model(self, cloud: bool = False) -> str:
        if cloud and self.fallback_model:
            return self.fallback_model
        return self.model
