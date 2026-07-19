"""Model adapters for Tortoise LLMExtractor — wraps different backends into the complete(system, user) interface."""

import os, json, requests

class OpenRouterModel:
    """Adapter for OpenRouter API — supports any model on the platform."""
    
    def __init__(self, model_id: str, max_tokens: int = 500, temperature: float = 0.0,
                 thinking_budget: int = 0):
        self.id = model_id
        self.api_key = os.environ.get('OPENROUTER_API_KEY', '')
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.thinking_budget = thinking_budget  # for reasoning models
        
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
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        # Enable thinking for reasoning models
        if self.thinking_budget > 0:
            body["reasoning"] = {"max_tokens": self.thinking_budget}
        
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
    'deepseek-flash': lambda: OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=500),
    'deepseek-v4-pro': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500),
    'deepseek-r1-xhigh': lambda: OpenRouterModel('deepseek/deepseek-r1-0528', max_tokens=500, thinking_budget=2000),
    'deepseek-v4-pro-xhigh': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500, temperature=0.0),
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
