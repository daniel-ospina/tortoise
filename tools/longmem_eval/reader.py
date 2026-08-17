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
    "the provided memory context. If the context does not contain enough "
    "information to answer, say that you do not know. Be concise; do not "
    "mention the context."
)


class Reader(Protocol):
    model_id: str

    def answer(self, *, context_hits: list[dict[str, Any]], question: str) -> str: ...


class LLMReader:
    """Reader backed by an OpenAI-compatible chat model."""

    def __init__(self, model, model_id: str):
        self._model = model
        self.model_id = model_id

    def answer(self, *, context_hits: list[dict[str, Any]], question: str) -> str:
        context = render_context(context_hits)
        user = (
            f"Memory context:\n{context}\n\n"
            f"Question: {question}\n\nAnswer:"
        )
        raw = self._model.complete(system=_SYSTEM_PROMPT, user=user)
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

    def answer(self, *, context_hits: list[dict[str, Any]], question: str) -> str:
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

    model = OpenAICompatModel(id=model_id, base_url=base_url, api_key_env=key_env)
    return LLMReader(model, model_id=model_id)
