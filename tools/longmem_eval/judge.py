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
                               judge model). The same official model is
                               served by OpenRouter: with only
                               ``OPENROUTER_API_KEY`` set, use
                               ``openrouter:openai/gpt-4o-2024-08-06``
    OPENAI_API_KEY             direct OpenAI key (NOT required when the
                               official model runs through OpenRouter etc.)

Abstention questions (``_abs`` in question_id) use the unanswerable-template.

Near-miss policy (issue #1949 decision record)
----------------------------------------------
reval3's 3b6f954b: hypothesis "University of Melbourne." vs gold
"University of Melbourne in Australia" — the core entity is right, the
geographic qualifier is missing. Under the official binary rubric this is
graded "no" (the subset rule: "If the response only contains a subset of
the information required by the answer, answer no.").

DECISION: KEEP STRICT. The anscheck templates are the benchmark's
verbatim — partial credit would change label semantics and break
comparability with published LongMemEval accuracies. The near-miss is
instead a *known rubric edge*: classified deterministically
(``classify_answer`` → ``AnswerGrade.NEAR_MISS``), still graded wrong, and
recorded at ~2% expected rate (1/50 in reval3). Tests pin
``NEAR_MISS_GRADING = "strict"`` and that the 3b6f954b shape grades False
(tests/test_longmem_runner.py — issue #1949 section).
"""
from __future__ import annotations  # noqa: I001

import enum
import json
import logging
import os
import urllib.request
from typing import Protocol

from tortoise.ingest import _PROVIDERS
# #2185 seam: the canonical usage-sink fire helper (same contract as the
# reader/product adapters — judge.py is tools-side; tortoise never imports it).
from tortoise.models import _emit_usage_sink

from .reader import _resolve_provider, _parse_model_spec

logger = logging.getLogger(__name__)

DEFAULT_JUDGE_MODEL = "openai:gpt-4o-2024-08-06"

# Issue #1949 decision record — judge near-miss (subset-rule) policy.
#
# reval3's 3b6f954b ("University of Melbourne." vs gold "University of
# Melbourne in Australia") is a near-miss: the core entity is correct, a
# required geographic qualifier is omitted. DECISION: KEEP STRICT. The
# anscheck templates are the benchmark's verbatim (published LongMemEval
# numbers are only comparable if the rubric is untouched); partial credit
# would change label semantics and muddy comparability. The near-miss is a
# *known* rubric edge instead — classified deterministically by
# ``classify_answer`` (AnswerGrade.NEAR_MISS), still graded wrong, and
# recorded at ~2% expected rate (1/50 in reval3). Do not change
# NEAR_MISS_GRADING without a new rubric decision.
NEAR_MISS_GRADING = "strict"

# Issue #2071 decision record — spot-check full-semantic grading
# (owner decision 2026-08-31, docs/planning/2026-08-31-2071-scoping-package.md).
#
# The product-lane QA spot-check (tools/ask_spotcheck.py) previously graded
# with a weaker lexical bar — word-overlap ``max(2, len(gold_words)//2)`` on
# UNIQUE words — that is STRUCTURALLY UNREACHABLE for rubric-style long-gold
# SSP questions (d6233ab6 79w / 1d4e3b97 68w / b0479f84 63w: a correct
# paraphrase never clears a ≥½-unique-word overlap bar). DECISION: the
# spot-check grades EVERY question with this semantic judge (``build_judge()``
# → the official gpt-4o anscheck — benchmark-identical to the graded eval
# lane); the lexical bar is DEMOTED to the key-free CI (MockJudge) substitute
# only. The graded eval is UNTOUCHED (``JUDGE_RUBRIC_ID "longmemeval-official"``
# and its fingerprint are unchanged — no bump). Historical spot-check
# aggregates (0.38/0.43) graded the 3 questions under the unreachable bar and
# are NOT directly comparable (runbook 1987-ask-abstention-check comparability
# note). The live spot-check fails fast (exit 2, naming the judge provider
# key) when no key is set — never a silent fallback to the removed bar.

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


class AnswerGrade(enum.Enum):
    """Deterministic grading class (issue #1949 near-miss pin).

    A 3-class projection of the official rubric's containment rule, used
    to *observe* the verdict deterministically (no LLM call):
    - CORRECT: the gold answer is contained in the hypothesis (clear right)
    - NEAR_MISS: the hypothesis is a strict subset of the gold — the
      response names the right entity but omits a required qualifier (the
      reval3 3b6f954b class: "University of Melbourne." vs gold
      "University of Melbourne in Australia")
    - WRONG: neither containment direction holds (clear wrong, wrong
      entity, or abstention)

    Strict grading is retained (NEAR_MISS_GRADING = "strict"): NEAR_MISS
    still grades *wrong* under the official binary rubric — the prompt's
    subset rule is explicit ("If the response only contains a subset of
    the information required by the answer, answer no."). This class is
    an observation layer only; it never alters the verdict.
    """

    CORRECT = "correct"
    NEAR_MISS = "near-miss"
    WRONG = "wrong"


_TRAILING_PUNCTUATION = ".,;:!?()[]{}\"'`’‘“”«»…-"


def _normalize_answer_text(text: str) -> str:
    """Deterministic normalization for subset/containment checks (issue
    #1949): lowercase, strip leading/trailing punctuation and whitespace,
    collapse internal whitespace. Internal punctuation is preserved (e.g.
    "St. Louis", "co-op") so normalization cannot merge distinct answers.

    Coerces non-string inputs (int, float, None) to str so that integer
    gold answers (e.g. temporal-reasoning Q71017276, gold=4) don't crash
    with AttributeError("'int' object has no attribute 'strip'") (#2450).
    """
    text = str(text) if text is not None else ""
    text = text.strip().lower()
    return " ".join(text.strip(_TRAILING_PUNCTUATION).split())


def classify_answer(answer: str, hypothesis: str) -> AnswerGrade:
    """Deterministic 3-class grading of a (gold, hypothesis) pair.

    Mirrors the containment rule the official judge prompt asks the LLM to
    apply: CORRECT iff the normalized gold is contained in the normalized
    hypothesis; NEAR_MISS iff the hypothesis is a strict subset of the
    gold (entity right, qualifier omitted — the 3b6f954b class); else
    WRONG. An empty answer or hypothesis grades WRONG (never NEAR_MISS —
    "" is a substring of everything).
    """
    gold = _normalize_answer_text(answer)
    hyp = _normalize_answer_text(hypothesis)
    if not gold or not hyp:
        return AnswerGrade.WRONG
    if gold in hyp:
        return AnswerGrade.CORRECT
    if hyp in gold:
        return AnswerGrade.NEAR_MISS
    return AnswerGrade.WRONG


def grade_label(answer: str, hypothesis: str) -> bool:
    """The strict binary verdict for a (gold, hypothesis) pair.

    True iff the gold is contained in the hypothesis (CORRECT). NEAR_MISS
    and WRONG both grade False — strict grading is pinned by the issue
    #1949 decision record (NEAR_MISS_GRADING = "strict").
    """
    return classify_answer(answer, hypothesis) is AnswerGrade.CORRECT


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
        # #2185: additive usage-capture seam (no-op unless the harness binds
        # it). NO last_* mirrors — the transport stays byte-verbatim so
        # published LongMemEval numbers stay comparable.
        self.usage_sink = None

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
            data = json.loads(r.read())
        # #2185 seam: fire with the response-local usage (provider None here —
        # bound at registration by the harness).
        _emit_usage_sink(self, data.get("usage"))
        return self.parse_response(data)


class LLMJudge:
    """Judge backed by an OpenAI-compatible chat model (official gpt-4o)."""

    def __init__(self, model, model_id: str, *, model_spec: str | None = None,
                 provider: str | None = None):
        self._model = model
        self.model_id = model_id
        # M2 (#1523): the full <provider>:<model> spec (set by build_judge) —
        # check_judge_key resolves the expected key env var from it.
        self.model_spec = model_spec or model_id
        # #2185 (A3): the resolved endpoint provider (set by build_judge) —
        # mirrors LLMReader.provider; the usage collector registers the
        # judge lane under this name so real judge spend prices against the
        # map (OfficialJudgeModel carries no provider of its own).
        self.provider = provider

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
    case-insensitive substring; see ``grade_label`` / ``classify_answer``,
    issue #1949 near-miss decision). Abstention: correct iff the response
    uses unanswerability markers (the LLM judge's criterion: "the model
    correctly identifies the question as unanswerable"). With the
    MockReader returning the evidence turns' content, this scores the full
    retrieval→reader→judge loop without any API keys.
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
        # #2027 (calibration): the reader's canonical abstention phrasings —
        # a strict subset of the product's ``_ABSTAINED_PHRASES`` (the
        # census authority; plan P2-32 pins judge ⊆ product). The minimal
        # Phase-2 abstention branch produces these forms on genuine
        # absence; without them the judge vocabulary gap scored correct
        # abstentions as failures (the runbook's 2 vocab-gap misses).
        "asked information is absent", "information is absent",
        "no mention of", "don't have that information",
        "don't have information", "absent from the context",
    )

    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool:
        if not hypothesis:
            return False
        if abstention:
            low = hypothesis.lower()
            return any(m in low for m in self._ABSTRACTION_MARKERS)
        # Strict deterministic rubric (issue #1949): grade_label delegates
        # to classify_answer so the near-miss (subset) class is pinned —
        # entity right, qualifier missing → False, consistently.
        return grade_label(answer, hypothesis)


class ScriptedSemanticJudge:
    """Deterministic scripted stand-in for the semantic LLM judge on offline
    lanes (issue #2071 CI fixtures / key-free harness runs).

    The live spot-check grades every question with the real semantic judge
    (``LLMJudge`` — the official gpt-4o anscheck); an offline lane cannot
    call an LLM, so this fake executes a pinned script of
    (hypothesis-substring → verdict) rules. The script IS the pinned
    expectation — a curated correct-paraphrase answer → True, a
    factually-wrong answer → False — so a reword of the pinned answers flips
    the fake loudly instead of silently passing (the compliant-model pattern,
    tests/test_reader_abstention_calibration.py). It accepts the full
    ``Judge.judge`` call shape; the script is keyed on the hypothesis only.
    """

    model_id = "scripted-semantic"

    def __init__(self, *, rules=(), default: bool = False):
        self._rules = [(needle.lower(), bool(v)) for needle, v in rules]
        self._default = bool(default)

    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool:
        del question_type, question, answer, abstention
        low = (hypothesis or "").lower()
        for needle, verdict in self._rules:
            if needle in low:
                return verdict
        return self._default

    def ping(self, probe: str) -> str:
        del probe
        return "scripted semantic ping ok"


def build_judge(spec: str | None = None, *, mock: bool = False) -> Judge:
    """Build the judge from env/config. ``mock=True`` returns MockJudge.

    Default model: ``openai:gpt-4o-2024-08-06`` (the official judge model).
    That default names OpenAI as the provider, but the SAME model is served
    by OpenRouter too — set
    ``TORTOISE_LME_JUDGE_MODEL=openrouter:openai/gpt-4o-2024-08-06`` to run
    the official judge through OpenRouter (the model id is what makes the
    judge official; the transport is a routing detail — external
    comparability is unchanged). Fails closed when no provider key is set
    and mock is off.
    """
    if mock:
        return MockJudge()
    raw_spec = spec or os.environ.get("TORTOISE_LME_JUDGE_MODEL", "").strip() or DEFAULT_JUDGE_MODEL
    provider, model_id = _parse_model_spec(raw_spec)
    resolved = _resolve_provider()
    if resolved is None:
        raise RuntimeError(
            "no LLM provider key configured for the LongMemEval judge "
            "(set OPENROUTER_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY / "
            "GEMINI_API_KEY). The official judge model is gpt-4o-2024-08-06; "
            "OpenRouter serves it — with only OPENROUTER_API_KEY set, point "
            "the spec there via "
            "TORTOISE_LME_JUDGE_MODEL=openrouter:openai/gpt-4o-2024-08-06. "
            "Or pass --mock for the offline mock judge.")
    _resolved_provider, base_url, key_env = resolved
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(
                f"unknown provider {provider!r} in {raw_spec!r}; "
                f"known: {sorted(_PROVIDERS)}")
        if not os.environ.get(_PROVIDERS[provider][1]):
            hint = ("" if provider != "openai" else
                    " — the SAME official model is served by OpenRouter; set "
                    "TORTOISE_LME_JUDGE_MODEL=openrouter:openai/gpt-4o-2024-08-06 "
                    "to run it with only OPENROUTER_API_KEY set")
            raise ValueError(
                f"model spec names provider {provider!r} but its key is not "
                f"set ({_PROVIDERS[provider][1]}){hint}")
    # Dedicated transport: the official judge call shape (no response_format,
    # no system message, max_tokens=10) — see OfficialJudgeModel.
    model = OfficialJudgeModel(
        id=model_id, base_url=base_url, api_key_env=key_env)
    return LLMJudge(model, model_id=model_id, model_spec=raw_spec,
                    provider=_resolved_provider)
