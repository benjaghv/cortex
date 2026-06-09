"""
cortex.stats
────────────
Running totals of tokens processed locally, and the money that would have cost
on a cloud model. Persisted to ~/.cortex/stats.json so the savings accumulate
across sessions and show in the banner.

Every litellm completion reports usage; cortex.agents.llm forwards it here.
"""

from __future__ import annotations

import json
from pathlib import Path

from cortex.config import CONFIG_DIR

STATS_FILE = CONFIG_DIR / "stats.json"

# Reference cloud price (USD per 1M tokens). Default ~ GPT-4o class.
# Override in ~/.cortex/config.toml via cost_ref_input_per_1m / _output_per_1m.
_DEFAULT_IN = 2.50
_DEFAULT_OUT = 10.00


def _load() -> dict:
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"prompt_tokens": 0, "completion_tokens": 0, "runs": 0}


def _save(data: dict) -> None:
    try:
        CONFIG_DIR.mkdir(exist_ok=True)
        STATS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass  # stats are best-effort; never break a run over them


def record(prompt_tokens: int, completion_tokens: int) -> None:
    """Add one completion's token usage to the persistent totals."""
    if not (prompt_tokens or completion_tokens):
        return
    data = _load()
    data["prompt_tokens"] = data.get("prompt_tokens", 0) + int(prompt_tokens or 0)
    data["completion_tokens"] = data.get("completion_tokens", 0) + int(completion_tokens or 0)
    _save(data)


def record_completion(resp) -> None:
    """Extract usage from a litellm response and record it. Best-effort."""
    try:
        usage = getattr(resp, "usage", None) or {}
        pt = getattr(usage, "prompt_tokens", None)
        ct = getattr(usage, "completion_tokens", None)
        if pt is None and isinstance(usage, dict):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        record(pt or 0, ct or 0)
    except Exception:
        pass


def bump_runs() -> None:
    data = _load()
    data["runs"] = data.get("runs", 0) + 1
    _save(data)


def summary(in_price_per_1m: "float | None" = None,
            out_price_per_1m: "float | None" = None) -> dict:
    """Totals + estimated USD saved vs a cloud model.

    Prices default to the config reference rates (cost_ref_*), falling back to
    GPT-4o-class defaults if config can't be read.
    """
    if in_price_per_1m is None or out_price_per_1m is None:
        try:
            from cortex.config import Settings
            cfg = Settings.load()
            in_price_per_1m = in_price_per_1m or cfg.cost_ref_input_per_1m
            out_price_per_1m = out_price_per_1m or cfg.cost_ref_output_per_1m
        except Exception:
            in_price_per_1m = in_price_per_1m or _DEFAULT_IN
            out_price_per_1m = out_price_per_1m or _DEFAULT_OUT
    d = _load()
    pt = d.get("prompt_tokens", 0)
    ct = d.get("completion_tokens", 0)
    saved = pt / 1_000_000 * in_price_per_1m + ct / 1_000_000 * out_price_per_1m
    return {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": pt + ct,
        "runs": d.get("runs", 0),
        "saved_usd": round(saved, 4),
    }


def reset() -> None:
    _save({"prompt_tokens": 0, "completion_tokens": 0, "runs": 0})
