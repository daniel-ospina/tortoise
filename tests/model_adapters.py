"""Model adapters for Tortoise LLMExtractor — re-export shim (#1530).

The eval-proven adapters moved to the production module ``tortoise/
model_adapters.py`` (OpenRouterModel, DeepSeekDirectModel, MODELS + the
routing/taxonomy layer) — production can no longer import tests/ (#1468
guard). This file re-exports the production names so the eval harness
(tests/eval_harness.py, tests/e018_harness.py, tools/longmem_eval/run.py,
tools/judge_harness.py, tools/probe_extractor.py, tools/experiments/
extractor-v2/*) keeps working unchanged. ``OllamaModel``/``OLLAMA_MODELS``
stay eval-local (local-only models, not a production adapter).
"""
from __future__ import annotations  # noqa: I001

import json  # noqa: F401 — eval scripts rely on the module importing json
import os  # noqa: F401
import requests

from tortoise.model_adapters import (  # noqa: F401
    DeepSeekDirectModel,
    MODELS,
    OpenRouterModel,
    RoutingModel,
    build_extractor_model,
    classify_llm_error,
    is_fatal,
    is_transient,
    resolve_extractor_provider,
)


class OllamaModel:
    """Adapter for local Ollama models."""

    def __init__(self, model_name: str, max_tokens: int = 500, temperature: float = 0.0):
        self.id = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        # M3 (#1524, GATE-2): per-call finish reason (Ollama done_reason).
        self.last_finish_reason: str | None = None

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        cap = self.max_tokens if max_tokens is None else max_tokens
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "num_predict": cap,
                "temperature": self.temperature,
            }
        }
        r = requests.post("http://localhost:11434/api/chat", json=body, timeout=120)
        r.raise_for_status()
        data = r.json()
        content = data.get('message', {}).get('content', '')
        self.last_prompt_tokens = data.get('prompt_eval_count', 0)
        self.last_completion_tokens = data.get('eval_count', 0)
        self.last_finish_reason = data.get('done_reason')
        return content


OLLAMA_MODELS = {
    'phi4-mini': lambda: OllamaModel('phi4-mini:latest', max_tokens=500),
}
