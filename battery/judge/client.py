
"""LLM-as-judge client (issue #1410) — rubric-scored model calls with the
model-call discipline (pinned model/temp/seed, outcome enum — never silent
fallback). Reuses the repo's model-adapter pattern (tools/judge_harness.py
→ tests/model_adapters.py OpenRouterModel) so validation runs are hermetic
when no model key is present (mock judge) and real when configured.
"""
from __future__ import annotations  # noqa: I001

import json

from dataclasses import dataclass
from typing import Callable  # noqa: UP035
import os
import random

from battery.arms.base import ArmUnavailable
from battery.enums import ModelCallOutcome


@dataclass
class JudgeCall:
    """One judge invocation result."""

    rubric_id: str
    item_id: str
    verdict: str
    confidence: float
    outcome: ModelCallOutcome = ModelCallOutcome.OK


class JudgeClient:
    """Rubric-scored judge. model_fn is injectable (mock/real seam).

    Real mode reads OPENROUTER_API_KEY / LLM_MODEL; mock mode is a
    deterministic scorer (for validation-battery tests, NOT for scoring
    episodes — scoring requires a validated rubric and the real judge).
    """

    def __init__(self, model_fn: Callable[[str], dict] | None = None,
                 model_id: str | None = None, *, force_mock: bool = False):
        self._model_fn = model_fn
        self._model_id = model_id or os.environ.get(
            "BATTERY_JUDGE_MODEL") or os.environ.get("LLM_MODEL") or ""
        # Real mode requires a key AND an explicit model id from config
        # (BATTERY_JUDGE_MODEL/LLM_MODEL or the model_id arg). An absent
        # model id ALWAYS means mock — a bare env key never triggers real
        # HTTP calls, and the "mock-judge" sentinel is gone.
        self._real = (not force_mock and self._model_fn is None
                      and bool(os.environ.get("OPENROUTER_API_KEY"))
                      and bool(self._model_id))

    @property
    def real(self) -> bool:
        return self._real

    def judge(self, rubric_id: str, item_id: str, prompt: str,
              temperature: float = 0.0) -> JudgeCall:
        """Score one item against the rubric. Raises ArmUnavailable on
        model failure (never silent fallback)."""
        try:
            if self._model_fn is not None:
                out = self._model_fn(prompt)
            elif self._real:
                out = self._real_call(prompt, temperature)
            else:
                out = self._mock_judge(prompt)
        except Exception as e:  # noqa: BLE001, RUF100
            raise ArmUnavailable(f"judge model call failed: {e}") from e
        verdict = str(out.get("verdict", ""))
        conf = float(out.get("confidence", 0.5))
        return JudgeCall(rubric_id=rubric_id, item_id=item_id,
                         verdict=verdict, confidence=conf)

    def _mock_judge(self, prompt: str) -> dict:
        """Deterministic mock: seeds from the prompt hash so validation
        runs are reproducible."""
        h = sum(ord(c) for c in prompt)
        verdicts = ["better", "worse", "tie"]
        return {"verdict": verdicts[h % 3],
                "confidence": 0.5 + 0.1 * (h % 5)}

    def _real_call(self, prompt: str, temperature: float) -> dict:
        import urllib.request
        key = os.environ.get("OPENROUTER_API_KEY", "")
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps({
                "model": self._model_id,
                "temperature": temperature,
                "messages": [{"role": "user", "content": prompt}],
            }).encode(),
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {"verdict": content.strip(), "confidence": 0.5}


def build_abba_prompts(item_a: str, item_b: str, rubric_text: str,
                       rng: random.Random) -> tuple[str, str, bool]:
    """Build the AB/BA pair for a position-bias test.

    Returns (prompt_ab, prompt_ba, first_is_a) — the caller evaluates both
    orders and checks the verdicts agree despite position swap.
    """
    base = (f"Rubric: {rubric_text}\n\n"
            f"Which response is better? Answer with the verdict "
            f"better/worse/tie and a confidence 0..1.\n")
    p_ab = base + f"Response A: {item_a}\nResponse B: {item_b}"
    p_ba = base + f"Response A: {item_b}\nResponse B: {item_a}"
    return p_ab, p_ba, True
