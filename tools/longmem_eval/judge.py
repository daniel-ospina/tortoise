"""Official LongMemEval answer-check judge (issue #1144, axis 2).

Reimplements the benchmark's official judge (``evaluate_qa.py`` —
``get_anscheck_prompt``) verbatim: task-specific yes/no templates judged by
``gpt-4o-2024-08-06`` at temperature 0, max_tokens 10, label = ``'yes' in
response.lower()``. The template text below is copied from the official
repository (MIT license) so published numbers are directly comparable to the
paper's reported accuracies.

The judge model is configured via env, never hardcoded:

    TORTOISE_LME_JUDGE_MODEL   judge model spec (default
                               ``openai:gpt-4o-2024-08-06`` — the official
                               judge model)
    OPENAI_API_KEY             (or another configured provider key)

Abstention questions (``_abs`` in question_id) use the unanswerable-template.
"""
from __future__ import annotations  # noqa: I001

import json
import logging
import os
import urllib.request
from typing import Protocol

from tortoise.ingest import _PROVIDERS

from .reader import _resolve_provider, _parse_model_spec

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "openai:gpt-4o-2024-08-06"

# Raw question_type values in the dataset → official answer-check template.
_TEMPLATES = (
    "single-session-user",
    "single-session-assistant",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
)


def is_abstention(question_id: str) -> bool:
    """A ``_abs`` suffix marks an abstention question (official convention)."""
    return "_abs" in question_id


def get_anscheck_prompt(
    question_type: str,
    question: str,
    answer: str,
    hypothesis: str,
    *,
    abstention: bool = False,
) -> str:
    """Official answer-check prompt (verbatim from LongMemEval evaluate_qa.py).

    Raises ValueError for unknown (non-abstention) question types, mirroring
    the official script's ``raise NotImplementedError``.
    """
    if not abstention:
        if question_type in ("single-session-user", "single-session-assistant",
                             "multi-session"):
            template = (
                "I will give you a question, a correct answer, and a response "
                "from a model. Please answer yes if the response contains the "
                "correct answer. Otherwise, answer no. If the response is "
                "equivalent to the correct answer or contains all the "
                "intermediate steps to get the correct answer, you should also "
                "answer yes. If the response only contains a subset of the "
                "information required by the answer, answer no. \n\n"
                "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, hypothesis)
        if question_type == "temporal-reasoning":
            template = (
                "I will give you a question, a correct answer, and a response "
                "from a model. Please answer yes if the response contains the "
                "correct answer. Otherwise, answer no. If the response is "
                "equivalent to the correct answer or contains all the "
                "intermediate steps to get the correct answer, you should also "
                "answer yes. If the response only contains a subset of the "
                "information required by the answer, answer no. In addition, do "
                "not penalize off-by-one errors for the number of days. If the "
                "question asks for the number of days/weeks/months, etc., and "
                "the model makes off-by-one errors (e.g., predicting 19 days "
                "when the answer is 18), the model's response is still correct."
                " \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: "
                "{}\n\nIs the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, hypothesis)
        if question_type == "knowledge-update":
            template = (
                "I will give you a question, a correct answer, and a response "
                "from a model. Please answer yes if the response contains the "
                "correct answer. Otherwise, answer no. If the response contains "
                "some previous information along with an updated answer, the "
                "response should be considered as correct as long as the "
                "updated answer is the required answer.\n\n"
                "Question: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, hypothesis)
        if question_type == "single-session-preference":
            template = (
                "I will give you a question, a rubric for desired personalized "
                "response, and a response from a model. Please answer yes if "
                "the response satisfies the desired response. Otherwise, answer "
                "no. The model does not need to reflect all the points in the "
                "rubric. The response is correct as long as it recalls and "
                "utilizes the user's personal information correctly.\n\n"
                "Question: {}\n\nRubric: {}\n\nModel Response: {}\n\n"
                "Is the model response correct? Answer yes or no only."
            )
            return template.format(question, answer, hypothesis)
        raise ValueError(f"unknown question_type: {question_type!r}")

    # Abstention
    template = (
        "I will give you an unanswerable question, an explanation, and a "
        "response from a model. Please answer yes if the model correctly "
        "identifies the question as unanswerable. The model could say that the "
        "information is incomplete, or some other information is given but the "
        "asked information is not.\n\n"
        "Question: {}\n\nExplanation: {}\n\nModel Response: {}\n\n"
        "Does the model correctly identify the question as unanswerable? "
        "Answer yes or no only."
    )
    return template.format(question, answer, hypothesis)


def _parse_judge_response(raw: str) -> bool:
    """Official label rule: ``'yes' in response.lower()``."""
    return "yes" in raw.lower()


class Judge(Protocol):
    model_id: str
    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool: ...

    def ping(self, probe: str) -> str: ...


class OfficialJudgeModel:
    """Exact official LongMemEval judge call shape (``evaluate_qa.py``).

    The official judge call is
    ``chat.completions.create(model=..., messages=[{"role": "user", ...}],
    n=1, temperature=0, max_tokens=10)`` — NO ``response_format`` (JSON
    mode), NO system message, temperature locked at 0, max_tokens locked at
    10. Published numbers are only comparable if the judge request matches
    this shape verbatim, so the judge uses this dedicated transport instead
    of the shared ``OpenAICompatModel`` (which forces ``response_format`` and
    a system message).
    """

    def __init__(self, *, id: str, base_url: str, api_key_env: str | None,
                 timeout: int = 60):
        self.id = id
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.timeout = timeout

    def build_request(self, user: str) -> dict:
        """The official kwargs — a single user message, nothing else."""
        return {
            "model": self.id,
            "messages": [{"role": "user", "content": user}],
            "n": 1,
            "temperature": 0,
            "max_tokens": 10,
        }

    @staticmethod
    def parse_response(data: dict) -> str:
        return data["choices"][0]["message"]["content"]

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise RuntimeError(
                    f"{self.id}: env var {self.api_key_env} is not set")
            h["Authorization"] = f"Bearer {key}"
        return h

    def complete(self, *, user: str) -> str:
        body = json.dumps(self.build_request(user)).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers=self._headers())
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return self.parse_response(json.loads(r.read()))


class LLMJudge:
    """Judge backed by an OpenAI-compatible chat model (official gpt-4o)."""

    def __init__(self, model, model_id: str, *, model_spec: str | None = None):
        self._model = model
        self.model_id = model_id
        # M2 (#1523): the full <provider>:<model> spec (set by build_judge) —
        # check_judge_key resolves the expected key env var from it.
        self.model_spec = model_spec or model_id

    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool:
        prompt = get_anscheck_prompt(
            question_type, question, answer, hypothesis, abstention=abstention)
        # Official call shape: the anscheck prompt is the ONLY message (no
        # system message, no JSON mode, max_tokens=10 — see OfficialJudgeModel).
        raw = self._model.complete(user=prompt)
        return _parse_judge_response(raw)

    def ping(self, probe: str) -> str:
        """Minimal transport-health probe (M2 #1523 pre-flight).

        One tiny completion through the judge's dedicated official transport
        (key + endpoint + model health only). HTTP status errors propagate
        for classification via the P2 taxonomy; mid-run judge 5xx/timeouts
        keep being retried by ``_call_with_backoff`` before a question is
        marked failed (verify-gate fix, never silent).
        """
        raw = self._model.complete(user=probe)
        return raw.strip()


class MockJudge:
    """Deterministic offline judge (CI smoke / wiring checks).

    Non-abstention: containment rule the LLM judge is asked to apply — the
    response is correct iff it contains the golden answer (normalized,
    case-insensitive substring). Abstention: correct iff the response uses
    unanswerability markers (the LLM judge's criterion: "the model correctly
    identifies the question as unanswerable"). With the MockReader returning
    the evidence turns' content, this scores the full retrieval→reader→judge
    loop without any API keys.
    """

    model_id = "mock-judge"

    def ping(self, probe: str) -> str:
        """Total protocol: pre-flight never pings the mock (mock mode skips
        the gate entirely), but the interface stays complete (M2 #1523)."""
        del probe
        return "mock ping ok"

    _ABSTRACTION_MARKERS = (
        "do not know", "don't know", "not know", "unanswerable",
        "incomplete", "not mention", "no information", "cannot answer",
        "can't answer", "not enough", "does not contain", "doesn't contain",
    )

    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool:
        if not hypothesis:
            return False
        if abstention:
            low = hypothesis.lower()
            return any(m in low for m in self._ABSTRACTION_MARKERS)
        if not answer:
            return False
        return answer.strip().lower() in hypothesis.lower()


def build_judge(spec: str | None = None, *, mock: bool = False) -> Judge:
    """Build the judge from env/config. ``mock=True`` returns MockJudge.

    Default model: ``openai:gpt-4o-2024-08-06`` (the official judge model).
    Fails closed when no provider key is set and mock is off.
    """
    if mock:
        return MockJudge()
    raw_spec = spec or os.environ.get("TORTOISE_LME_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
    provider, model_id = _parse_model_spec(raw_spec)
    resolved = _resolve_provider()
    if resolved is None:
        raise RuntimeError(
            "no LLM provider key configured for the LongMemEval judge "
            "(set OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY — the official judge is gpt-4o via OPENAI_API_KEY — "
            "or pass --mock for the offline mock judge)")
    _resolved_provider, base_url, key_env = resolved
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(
                f"unknown provider {provider!r} in {raw_spec!r}; "
                f"known: {sorted(_PROVIDERS)}")
        if not os.environ.get(_PROVIDERS[provider][1]):
            raise ValueError(
                f"model spec names provider {provider!r} but its key is not "
                f"set ({_PROVIDERS[provider][1]})")
    # Dedicated transport: the official judge call shape (no response_format,
    # no system message, max_tokens=10) — see OfficialJudgeModel.
    model = OfficialJudgeModel(
        id=model_id, base_url=base_url, api_key_env=key_env)
    return LLMJudge(model, model_id=model_id, model_spec=raw_spec)
