
"""LLM-as-judge client (issue #1410) — rubric-scored model calls with the
model-call discipline (pinned model/temp/seed, outcome enum — never silent
fallback). Reuses the repo's model-adapter pattern (tools/judge_harness.py
→ tests/model_adapters.py OpenRouterModel) so validation runs are hermetic
when no model key is present (mock judge) and real when configured.
"""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from typing import Callable  # noqa: UP035

from battery.arms.base import ArmUnavailable
from battery.config.prices import RATES_PER_1M_USD
from battery.enums import ModelCallOutcome
from battery.exceptions import ConfigError


@dataclass
class JudgeCall:
    """One judge invocation result."""

    rubric_id: str
    item_id: str
    verdict: str
    confidence: float
    outcome: ModelCallOutcome = ModelCallOutcome.OK
    #: Usage capture (#2292 Task 3): real OpenRouter usage block parsed into
    #: the JudgeCall so judge spend is METERED (decision (c) — judge spend
    #: accumulates under its own line, never folded into the model-under-test
    #: row). Zero on mock calls.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


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

    @property
    def model_id(self) -> str:
        """Resolved model id ("" in mock mode). Public so the evidence
        validation pair-guard can compare the two judge configs."""
        return self._model_id

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
                         verdict=_canonical_verdict(verdict),
                         confidence=conf,
                         prompt_tokens=int(out.get("prompt_tokens", 0) or 0),
                         completion_tokens=int(
                             out.get("completion_tokens", 0) or 0),
                         cost_usd=float(out.get("cost_usd", 0.0) or 0.0))

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
        usage = data.get("usage") or {}
        # Metered judge spend (decision (c)): parse the OpenRouter usage
        # block so the --evidence path can HARD-STOP against the reserve.
        # Fail-closed on an ABSENT usage block (review #2575 pro-gate
        # P2-1): usage-missing responses are unmetered — pt=ct=0 would
        # cost $0.00 and sail past the reserve. A real judge call always
        # bills tokens; usage absence means the meter cannot see spend.
        if not usage:
            raise ConfigError(
                "judge response carries NO usage block — spend unmetered; "
                "fail-closed (reserve HARD STOP cannot meter an absent "
                "usage)")
        pt = int(usage.get("prompt_tokens", 0) or 0)
        ct = int(usage.get("completion_tokens", 0) or 0)
        cost = float(usage.get("cost", 0.0)
                     or _openrouter_cost(data.get("model", ""), pt, ct))
        try:
            parsed = json.loads(content)
            parsed.setdefault("prompt_tokens", pt)
            parsed.setdefault("completion_tokens", ct)
            parsed.setdefault("cost_usd", cost)
            return parsed
        except json.JSONDecodeError:
            return {"verdict": content.strip(), "confidence": 0.5,
                    "prompt_tokens": pt, "completion_tokens": ct,
                    "cost_usd": cost}


_VERDICT_WORD = re.compile(r"\b(yes|no|better|worse|tie)\b")


def _canonical_verdict(raw: str) -> str:
    """Normalize a model verdict into the canonical vocabulary label.

    Real judges answer plain text ("YES", "No.", "The answer is NO
    because ...") not always JSON — case/punctuation/prose noise would
    otherwise make byte-identical retest prompts disagree and collapse the
    retest/kappa/gold legs on parse artifacts, never on judge behavior.
    """
    if not raw:
        return ""
    m = _VERDICT_WORD.search(raw.strip().lower())
    return m.group(1) if m else raw.strip()


def _openrouter_cost(model: str, pt: int, ct: int) -> float:
    """OpenRouter per-1M-token price table (fallback when the usage block
    omits ``cost``). Judge-model rows only — approximate is fine for a
    reserve HARD STOP (the cap is a guard, never a bill)."""
    prices = {
        "gpt-4o": (2.50, 10.00), "gpt-4o-2024-08-06": (2.50, 10.00),
        "opus": (15.00, 75.00), "claude": (3.00, 15.00),
        # #2874: the ONE declared basis (battery/config/prices.py) — this row
        # used to be a local copy that had drifted from every real price.
        "deepseek": RATES_PER_1M_USD,
    }
    p_in, p_out = (15.00, 75.00)  # fail-closed default: the table MAX — an
    # unknown judge model is NEVER unmetered (a 0-cost fallback would let an
    # unmetered model sail past the reserve; over-estimating trips the HARD
    # STOP early, the safe direction). Review #2575 B-P2.
    for key, (i_, o_) in prices.items():
        if key in model.lower():
            p_in, p_out = i_, o_
            break
    return (pt * p_in + ct * p_out) / 1_000_000.0


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
