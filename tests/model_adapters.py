"""Model adapters for Tortoise LLMExtractor — wraps different backends into the complete(system, user) interface."""
from __future__ import annotations

import os, json, requests

class OpenRouterModel:
    """Adapter for OpenRouter API — supports any model on the platform."""
    
    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0,
                 thinking_budget: int = 0, disable_reasoning: bool = False):
        self.id = model_id
        self.api_key = os.environ.get('OPENROUTER_API_KEY', '')
        self.max_tokens = max_tokens  # None = NO CAP (omit from request body)
        self.temperature = temperature
        self.thinking_budget = thinking_budget  # for reasoning models
        self.disable_reasoning = disable_reasoning  # send reasoning.effort=none
        
    def complete(self, *, system: str, user: str) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        # Enable thinking for reasoning models
        if self.thinking_budget > 0:
            body["reasoning"] = {"max_tokens": self.thinking_budget}
        elif self.disable_reasoning:
            body["reasoning"] = {"effort": "none"}
        
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers, json=body, timeout=60
        )
        r.raise_for_status()
        data = r.json()
        
        # Extract usage for cost tracking
        usage = data.get('usage', {})
        self.last_prompt_tokens = usage.get('prompt_tokens', 0)
        self.last_completion_tokens = usage.get('completion_tokens', 0)
        self.last_cost = data.get('usage', {}).get('total_tokens', 0)  # will be overridden
        
        content = data['choices'][0]['message']['content']
        return content

# Pre-configured models
MODELS = {
    'deepseek-flash': lambda: OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=None, temperature=0.0),
    'deepseek-flash-direct': lambda: DeepSeekDirectModel('deepseek-v4-flash', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro-direct': lambda: DeepSeekDirectModel('deepseek-v4-pro', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500),
    'deepseek-r1-xhigh': lambda: OpenRouterModel('deepseek/deepseek-r1-0528', max_tokens=500, thinking_budget=2000),
    'deepseek-v4-pro-xhigh': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500, temperature=0.0),
    # OpenRouter-side only (epic #909 gate judges) — independent of Pi's qwen-tp provider config.
    # Reasoning models consume the shared max_tokens budget on internal reasoning; bound it
    # (thinking_budget) or disable it (disable_reasoning) so the label JSON actually gets emitted
    # (#946 gate runs observed all-reasoning/zero-content collapses at default settings).
    # ⚠️ judge_harness._apply_tuning OVERWRITES max_tokens with its CLI default (2000) — these
    # registry tunings are inert unless the CLI passes an explicit --max-tokens >= the value here
    # (the gate run used --max-tokens 12000 / 8000; a bare `--model qwen3.8-max` would starve).
    'qwen3.8-max': lambda: OpenRouterModel('qwen/qwen3.8-max', max_tokens=8000, temperature=0.0, thinking_budget=2000),
    'solar-pro4': lambda: OpenRouterModel('upstage/solar-pro4', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro-noreason': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=8000, temperature=0.0, disable_reasoning=True),
    'claude-opus-5': lambda: OpenRouterModel('anthropic/claude-opus-5', max_tokens=12000, temperature=0.0),
}

import requests, json

class OllamaModel:
    """Adapter for local Ollama models."""
    
    def __init__(self, model_name: str, max_tokens: int = 500, temperature: float = 0.0):
        self.id = model_name
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
    
    def complete(self, *, system: str, user: str) -> str:
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "num_predict": self.max_tokens,
                "temperature": self.temperature,
            }
        }
        r = requests.post("http://localhost:11434/api/chat", json=body, timeout=120)
        r.raise_for_status()
        data = r.json()
        content = data.get('message', {}).get('content', '')
        self.last_prompt_tokens = data.get('prompt_eval_count', 0)
        self.last_completion_tokens = data.get('eval_count', 0)
        return content

OLLAMA_MODELS = {
    'phi4-mini': lambda: OllamaModel('phi4-mini:latest', max_tokens=500),
}


class DeepSeekDirectModel(OpenRouterModel):
    """Direct DeepSeek API adapter (api.deepseek.com) — same model ids as
    OpenRouter (deepseek-v4-flash / v4-pro) but no OpenRouter hop. Used when
    DEEPSEEK_API_KEY is set and TORTOISE_EXTRACTOR_PROVIDER != 'openrouter'
    (#1350 — the extractor's LLM calls were hitting OpenRouter connection
    errors under load; the direct API is the same model, different route)."""

    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0, **kw):
        super().__init__(model_id, max_tokens=max_tokens,
                         temperature=temperature, **kw)
        self.api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    def complete(self, *, system: str, user: str) -> str:
        import requests as _r
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        r = _r.post("https://api.deepseek.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=60)
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        return data["choices"][0]["message"]["content"]

