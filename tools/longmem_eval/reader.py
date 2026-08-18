"""Reader LLM — answers each LongMemEval question from retrieved context
(issue #1144, axis 2).

Uses the repo's existing provider pattern (``tortoise.ingest._PROVIDERS`` +
``OpenAICompatModel`` — the same wiring as session LLM extraction) so the
runner needs no new credentials machinery. Configuration is env-driven, never
hardcoded:

    TORTOISE_LME_READER_MODEL   reader model spec: ``<provider>:<model>`` or
                                bare ``<model>`` resolved against the first
                                configured provider key
                                (default: ``openrouter:deepseek/deepseek-chat``,
                                the repo's cheap-tier session-extraction pick)
    OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY
                                provider keys (existing repo pattern)

The offline mock (``--mock``) returns the first evidence turn (``has_answer``
stamp) found in the retrieved hits — a deterministic stand-in proving the
retrieval actually delivered the evidence, with zero network and no keys
(CI smoke).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from tortoise.ingest import _PROVIDERS
from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY

from .retrieve import render_context

logger = logging.getLogger(__name__)

DEFAULT_READER_MODEL = "openrouter:deepseek/deepseek-chat"

# Provider priority for the READER when multiple keys are set — reuse the
# session-extraction order (sdk._SESSION_LLM_PROVIDER_PRIORITY).
_PROVIDER_PRIORITY = _SESSION_LLM_PROVIDER_PRIORITY

_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to retrieved memory context "
    "from a user's long chat history. Answer the user's question using ONLY "
    "the provided memory context. The context includes dated chat sessions "
    "and, when applicable, a 'Current Date' header. When the context contains "
    "the information needed to answer, give a direct, concrete answer — do "
    "not hedge, refuse, or say you do not know. Only say you do not know when "
    "the context genuinely lacks the information. Be concise; do not mention "
    "the context."
)

# Type-specific instructions appended to the system prompt for the two
# categories that fail at the READER (issue #1366: preference 43%, temporal
# 62% — evidence IS retrieved, the reader hedges/miscounts). The generic
# prompt above already flips the default toward committing; these fragments
# give the reader the exact reasoning it must perform.

_TEMPORAL_FRAGMENT = (
    "\n\nTEMPORAL REASONING INSTRUCTIONS: this question asks about elapsed "
    "time or ordering (e.g. how many days/weeks/months ago). Use the "
    "'Current Date: YYYY-MM-DD' header and the per-session 'session date "
    "YYYY-MM-DD' annotations in the context to compute the elapsed time "
    "between the relevant session date and the current date. Commit to a "
    "specific numeric answer (e.g. '3 days ago') — do not hedge or refuse "
    "when the dated evidence of the event/statement in question is present "
    "in the context. An off-by-one error in the day count is acceptable. "
    "However, if the context contains NO dated evidence of the event or "
    "statement the question asks about, say you do not know rather than "
    "guessing."
)

_PREFERENCE_FRAGMENT = (
    "\n\nPREFERENCE INSTRUCTIONS: this question asks which option the user "
    "prefers. Find the user's turns discussing the options and commit to "
    "the specific option the user stated or implied a preference for "
    "(e.g. 'X is fine but I prefer Y' → Y). Answer with that option — do "
    "not hedge, refuse, or say you do not know when the user's preference "
    "appears in the context."
)

# question_type → the fragment that unlocks correct reasoning for it.
_TYPE_FRAGMENTS: dict[str, str] = {
    "temporal-reasoning": _TEMPORAL_FRAGMENT,
    "single-session-preference": _PREFERENCE_FRAGMENT,
}


def system_prompt_for(question_type: str | None) -> str:
    """The reader system prompt for a question, type-tailored.

    Unknown/absent types get the hardened generic prompt; temporal-reasoning
    and single-session-preference append their reasoning instructions (the
    weak categories from issue #1366).
    """
    if not question_type:
        return _SYSTEM_PROMPT
    return _SYSTEM_PROMPT + _TYPE_FRAGMENTS.get(question_type, "")

# Official gen.py default generation length for non-CoT runs (the reader's
# answer prompt is answered at temperature 0, max_tokens 500 — the official
# call shape, no JSON mode; see build_reader).
DEFAULT_READER_MAX_TOKENS = 500


class Reader(Protocol):
    model_id: str

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str: ...


class LLMReader:
    """Reader backed by an OpenAI-compatible chat model."""

    def __init__(self, model, model_id: str):
        self._model = model
        self.model_id = model_id

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str:
        # The context carries the official gen.py shape: a "Current Date:
        # {question_date}" header + per-session date annotations (see
        # retrieve.render_context) — temporal-reasoning questions are
        # structurally unanswerable without them (P1 #1144). The system
        # prompt is type-tailored (#1366): temporal-reasoning and
        # single-session-preference get reasoning instructions that counter
        # the reader's documented hedging/miscounting failures.
        context = render_context(context_hits, question_date=question_date)
        user = (
            f"Memory context:\n{context}\n\n"
            f"Question: {question}\n\nAnswer:"
        )
        raw = self._model.complete(
            system=system_prompt_for(question_type), user=user)
        return raw.strip()


class MockReader:
    """Deterministic offline reader (CI smoke / wiring checks).

    Returns the CONCATENATION of every retrieved hit stamped ``has_answer``
    (evidence turns; their content embeds the golden answer in the committed
    mini fixture — multi-session evidence may span several turns), else the
    first retrieved hit, else empty. A judge that keys on the golden answer
    then scores retrieval failures as misses, honestly, with no network and
    no keys.
    """

    model_id = "mock-reader"

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str:
        # question_type is accepted for protocol parity with LLMReader (the
        # runner forwards it unconditionally); the mock answers from evidence
        # turns regardless of type.
        del question_type
        evidence = [
            str(hit["content"]).strip()
            for hit in context_hits
            if hit.get("has_answer") and str(hit.get("content", "")).strip()
        ]
        if evidence:
            return " ".join(evidence)
        for hit in context_hits:
            if str(hit.get("content", "")).strip():
                return str(hit["content"]).strip()
        return ""


def _resolve_provider() -> tuple[str, str] | None:
    """Return (base_url, key_env) of the first configured provider, or None."""
    for provider in _PROVIDER_PRIORITY:
        base_url, key_env = _PROVIDERS[provider]
        if os.environ.get(key_env):
            return base_url, key_env
    return None


def _parse_model_spec(spec: str) -> tuple[str | None, str]:
    """Split ``<provider>:<model>`` → (provider, model); bare → (None, model)."""
    p, sep, m = spec.partition(":")
    if sep and p and m:
        return p, m
    return None, spec


def build_reader(spec: str | None = None, *, mock: bool = False) -> Reader:
    """Build the reader from env/config. ``mock=True`` returns MockReader.

    Raises RuntimeError when no provider key is configured and mock is off —
    fail-closed, mirroring ``capture_session``'s no-key posture.
    """
    if mock:
        return MockReader()
    raw_spec = spec or os.environ.get("TORTOISE_LME_READER_MODEL", "").strip() or DEFAULT_READER_MODEL
    provider, model_id = _parse_model_spec(raw_spec)
    resolved = _resolve_provider()
    if resolved is None:
        raise RuntimeError(
            "no LLM provider key configured for the LongMemEval reader "
            "(set OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY, or pass --mock for the offline mock reader)")
    base_url, key_env = resolved
    if provider is not None:
        if provider not in _PROVIDERS:
            raise ValueError(
                f"unknown provider {provider!r} in {raw_spec!r}; "
                f"known: {sorted(_PROVIDERS)}")
        if not os.environ.get(_PROVIDERS[provider][1]):
            raise ValueError(
                f"model spec names provider {provider!r} but its key is not "
                f"set ({_PROVIDERS[provider][1]})")
    from tortoise.models import OpenAICompatModel

    # Official reader call shape: temperature 0, bounded max_tokens, NO
    # response_format (JSON mode) — the answer is free text, and forcing
    # json_object mangles free-form answers on several providers.
    model = OpenAICompatModel(
        id=model_id, base_url=base_url, api_key_env=key_env,
        response_format=None, max_tokens=DEFAULT_READER_MAX_TOKENS,
    )
    return LLMReader(model, model_id=model_id)
