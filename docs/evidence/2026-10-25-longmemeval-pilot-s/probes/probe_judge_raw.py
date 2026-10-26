"""Dump the RAW judge transport response (official call shape) per candidate,
so an empty/truncated/reasoning-only response is visible instead of being
silently graded as "no"."""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# Run from the repo root: `python probes/<name>`; the repo root is
# resolved from this file so the probe always tests THIS tree.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))

from tools.longmem_eval.judge import get_anscheck_prompt

PROMPT = get_anscheck_prompt(
    "single-session-user",
    "What did I buy for my sister's birthday?",
    "a silver necklace",
    "You bought a silver necklace for your sister.",
)

CANDIDATES = [
    # (provider, base_url, key_env, model_id, extra request body)
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
     "deepseek-flash", {}),
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
     "deepseek-v4-pro", {}),
    # The same two ids with the vendor's own documented thinking control off.
    # This is the arm that decides the fix: if `content` becomes a real yes/no,
    # the empty answer was the transport starving the official 10-token budget,
    # never the model's judgment.
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
     "deepseek-flash", {"thinking": {"type": "disabled"}}),
    ("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
     "deepseek-v4-pro", {"thinking": {"type": "disabled"}}),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
     "GEMINI_API_KEY", "gemini-2.5-flash-lite", {}),
]

for provider, base, key_env, model, extra in CANDIDATES:
    body = {"model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "n": 1,
            "temperature": 0,
            "max_tokens": 10}
    body.update(extra)
    body = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ[key_env]}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        msg = data["choices"][0]["message"]
        print(f"\n=== {provider}:{model} extra={extra or '-'} OK")
        print("  content:", repr(msg.get("content")))
        print("  reasoning:", repr(msg.get("reasoning_content") or msg.get("reasoning"))[:120])
        print("  finish:", data["choices"][0].get("finish_reason"))
        print("  usage:", json.dumps(data.get("usage")))
    except urllib.error.HTTPError as e:
        print(f"\n=== {provider}:{model} extra={extra or '-'} HTTP {e.code}")
        print("  " + e.read().decode()[:500])
    except Exception as e:
        print(f"\n=== {provider}:{model} extra={extra or '-'} "
              f"ERR {type(e).__name__}: {e}")
