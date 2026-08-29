"""Reader LLM — answers each LongMemEval question from retrieved context
(issue #1144, axis 2).

The eval reader is now a THIN RE-EXPORT of the product reader
(``tortoise.reader`` — the #1987 inversion, the #1983 pattern): the product
owns ALL reader prompt text and the ``LLMReader`` class; this module keeps
only the eval-only layer (``READER_MODEL`` pin, ``MockReader``,
``build_reader`` env/CLI resolution + ``OpenAICompatModel`` wiring,
``_resolve_provider``, ``_parse_model_spec``, the preflight ping via the
product's ``PROBE_SYSTEM``). Prompt drift between the eval and the product
is impossible by construction.

A1 (#1546 + #1762 + #1775): the universal partial-knowledge abstention
clause (``_ABSTRACTION_FRAGMENT``) lets the reader derive unanswerability
from the evidence — the ``_abs`` question_id marker never crosses into the
reader path. #1762 tightened the clause so the reader commits whenever the
asked value is present in context; #1775 restructures it into an explicit
two-phase decision (presence-commit first, abstention only on genuine
absence) so partial evidence with the value present commits instead of
hedging (the reval3 class).
"""
# ═════════════════════════════════════════════════════════════════════════
# ══ HARNESS PURPOSE — READ THIS FIRST ════════════════════════════════════
# tools/longmem_eval/ is a THIN MEASUREMENT LAYER over the product
# (tortoise/): the eval calls the product's OWN engine and measures it.
# Quality improvements belong IN tortoise/ (that is what ships to
# customers). The READER (this module) re-exports the PRODUCT reader
# (tortoise/reader.py — shipped in #1987 as the /v1/ask + SDK ask() +
# MCP tortoise_ask answer surface); the eval measures the exact shipped
# prompts and reader class.
# See docs/audit/2026-08-29-product-cohesion.md for the full audit.
# ═════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import os
import sys

from tortoise.ingest import _PROVIDERS
from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY
# The product owns the reader (prompt constants + LLMReader + protocol +
# PROBE_SYSTEM). Re-exported verbatim — the private prompt constants are
# part of the eval-test contract (tests/test_longmem_reader_prompting.py,
# tests/test_longmem_reader_aggregation.py, tests/test_longmem_reader_pinning.py
# import them directly).
from tortoise.reader import (  # noqa: F401
    DEFAULT_READER_MAX_TOKENS,
    LLMReader,
    PROBE_SYSTEM,
    Reader,
    _ABSTRACTION_FRAGMENT,
    _KNOWLEDGE_UPDATE_FRAGMENT,
    _MULTI_SESSION_FRAGMENT,
    _SYSTEM_PROMPT,
    _TYPE_FRAGMENTS,
    reader_prompt_constants,
    system_prompt_for,
)

__all__ = [
    "DEFAULT_READER_MAX_TOKENS", "LLMReader", "PROBE_SYSTEM", "Reader",
    "READER_MODEL", "MockReader", "build_reader", "_resolve_provider",
    "_parse_model_spec", "_SYSTEM_PROMPT", "_TYPE_FRAGMENTS",
    "_KNOWLEDGE_UPDATE_FRAGMENT", "_MULTI_SESSION_FRAGMENT",
    "_ABSTRACTION_FRAGMENT", "reader_prompt_constants", "system_prompt_for",
]

# Pinned reader identity for the run (M5 #1525). The V2 runs were confounded
# by reader-model drift between runs (deepseek-chat → deepseek-v4-flash); the
# pin makes the code default equal what runs use. Any override is recorded
# (reader_pinned=false) + warned on stderr — never silent.
READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"

# Provider priority for the READER when multiple keys are set — reuse the
# session-extraction order (sdk._SESSION_LLM_PROVIDER_PRIORITY).
_PROVIDER_PRIORITY = _SESSION_LLM_PROVIDER_PRIORITY


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

    def answer(self, *, context_hits: list[dict], question: str,
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
