"""Reader LLM — answers each LongMemEval question from retrieved context
(issue #1144, axis 2).

Uses the repo's existing provider pattern (``tortoise.ingest._PROVIDERS`` +
``OpenAICompatModel`` — the same wiring as session LLM extraction) so the
runner needs no new credentials machinery. Configuration is env-driven, never
hardcoded:

    TORTOISE_LME_READER_MODEL   reader model spec: ``<provider>:<model>`` or
                                bare ``<model>`` resolved against the first
                                configured provider key
                                (default: ``openrouter:deepseek/deepseek-v4-flash``
                                — the M5 pinned reader identity, #1525)
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
import sys
from typing import Any, Protocol

from tortoise.ingest import _PROVIDERS
from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY

from .retrieve import render_context

logger = logging.getLogger(__name__)

# Pinned reader identity for the run (M5 #1525). The V2 runs were confounded
# by reader-model drift between runs (deepseek-chat → deepseek-v4-flash); the
# pin makes the code default equal what runs use. Any override is recorded
# (reader_pinned=false) + warned on stderr — never silent.
READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"

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


def reader_prompt_constants() -> tuple[str, dict[str, str]]:
    """The run's reader prompt constants (M5): the generic system prompt +
    type fragments, recorded verbatim in report methodology so prompt drift
    across run cells is visible in the report. A dict copy keeps future
    fragment additions (A1 abstention, A2 aggregation — same epic) additive."""
    return _SYSTEM_PROMPT, dict(_TYPE_FRAGMENTS)


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
    model_spec: str | None = None
    provider: str | None = None
    pinned: bool | None = None

    def answer(self, *, context_hits: list[dict[str, Any]], question: str,
               question_date: str | None = None,
               question_type: str | None = None) -> str: ...

    def ping(self, probe: str) -> str: ...


class LLMReader:
    """Reader backed by an OpenAI-compatible chat model.

    M5 (#1525): carries its resolved identity — ``model_spec`` (the full
    ``<provider>:<model>`` spec actually used, post env/CLI resolution),
    ``provider`` (the resolved endpoint provider — the truth about where the
    call goes) and ``pinned`` (bool: spec == ``READER_MODEL``) — set by
    ``build_reader`` and recorded in the report methodology.
    """

    def __init__(self, model, model_id: str, *, model_spec: str | None = None,
                 provider: str | None = None, pinned: bool | None = None):
        self._model = model
        self.model_id = model_id
        self.model_spec = model_spec or model_id
        self.provider = provider
        self.pinned = pinned

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

    def ping(self, probe: str) -> str:
        """Minimal transport-health probe (M2 #1523 pre-flight).

        One tiny completion through the reader's OWN model (key + endpoint +
        model health only — prompt/context rendering is exercised on question
        1). HTTP status errors propagate for classification; preflight.py
        maps them via the P2 taxonomy.
        """
        from .preflight import PROBE_SYSTEM
        raw = self._model.complete(system=PROBE_SYSTEM, user=probe)
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
    # M5 (#1525): mock runs aren't model runs — pin is N/A (None); the
    # methodology records model_spec=model_id + provider="mock" so a mock
    # report is never mistaken for a pinned real-model report.
    model_spec = "mock-reader"
    provider = "mock"
    pinned = None

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

    def ping(self, probe: str) -> str:
        """Total protocol: pre-flight never pings the mock (mock mode skips
        the gate entirely), but the interface stays complete (M2 #1523)."""
        del probe
        return "mock ping ok"


def _resolve_provider(named: str | None = None) -> tuple[str, str, str] | None:
    """Return (provider, base_url, key_env) of the endpoint that will serve
    the reader: the spec's named provider (its key is validated by the
    caller) when named, else the first configured provider in priority
    order. Fixes the M5 endpoint-truth gap: the recorded provider must be
    the endpoint actually used."""
    if named is not None:
        base_url, key_env = _PROVIDERS[named]
        return named, base_url, key_env
    for provider in _PROVIDER_PRIORITY:
        base_url, key_env = _PROVIDERS[provider]
        if os.environ.get(key_env):
            return provider, base_url, key_env
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
    raw_spec = spec or os.environ.get("TORTOISE_LME_READER_MODEL", "").strip() or READER_MODEL
    provider, model_id = _parse_model_spec(raw_spec)
    if provider is not None and provider not in _PROVIDERS:
        raise ValueError(
            f"unknown provider {provider!r} in {raw_spec!r}; "
            f"known: {sorted(_PROVIDERS)}")
    # Fail-closed no-key posture (pre-M5 behavior, locked by tests): NO
    # configured key at all → RuntimeError, regardless of what the spec
    # names. A named-provider key that is missing (while some OTHER key is
    # set) is a ValueError below.
    if not any(os.environ.get(_PROVIDERS[p][1]) for p in _PROVIDER_PRIORITY):
        raise RuntimeError(
            "no LLM provider key configured for the LongMemEval reader "
            "(set OPENROUTER_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY / "
            "GEMINI_API_KEY, or pass --mock for the offline mock reader)")
    if provider is not None and not os.environ.get(_PROVIDERS[provider][1]):
        raise ValueError(
            f"model spec names provider {provider!r} but its key is not "
            f"set ({_PROVIDERS[provider][1]})")
    provider_name, base_url, key_env = _resolve_provider(named=provider)
    if raw_spec != READER_MODEL:
        print(f"[longmem_eval] WARNING: reader model spec {raw_spec!r} != "
              f"pinned READER_MODEL {READER_MODEL!r} — run is NOT pinned "
              f"(M5 #1525); report records reader_pinned=false",
              file=sys.stderr)
    from tortoise.models import OpenAICompatModel

    # Official reader call shape: temperature 0, bounded max_tokens, NO
    # response_format (JSON mode) — the answer is free text, and forcing
    # json_object mangles free-form answers on several providers.
    model = OpenAICompatModel(
        id=model_id, base_url=base_url, api_key_env=key_env,
        response_format=None, max_tokens=DEFAULT_READER_MAX_TOKENS,
    )
    return LLMReader(model, model_id=model_id, model_spec=raw_spec,
                     provider=provider_name,
                     pinned=(raw_spec == READER_MODEL))
