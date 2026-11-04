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
# (tortoise/reader.py — shipped in #1987; per #3849 it is no longer a
# product surface: no /v1/ask route, no SDK ask() method, no MCP tool).
# Its direct importers are the EVAL-ONLY ask lane (tortoise/ask_lane.py),
# this thin re-export, and the eval tooling that measures the reader
# directly (tools/longmem_eval/run.py, context_assembly_arms.py,
# measure_temporal.py, preflight.py, detector_parity.py, and
# tools/gen_ask_transcripts.py); the eval measures the exact shipped
# prompts and reader class.
# See docs/audit/2026-08-29-product-cohesion.md for the full audit.
# ═════════════════════════════════════════════════════════════════════════
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/reader.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import os

from tortoise.ingest import _PROVIDERS
from tortoise.models import OpenAICompatModel

# The product owns the reader (prompt constants + LLMReader + protocol +
# PROBE_SYSTEM). Re-exported verbatim — the private prompt constants are
# part of the eval-test contract (tests/test_longmem_reader_prompting.py,
# tests/test_longmem_reader_aggregation.py, tests/test_longmem_reader_pinning.py
# import them directly).
from tortoise.reader import (
    _ABSTRACTION_FRAGMENT,
    _KNOWLEDGE_UPDATE_FRAGMENT,
    _MULTI_SESSION_FRAGMENT,
    _SYSTEM_PROMPT,
    _TYPE_FRAGMENTS,
    DEFAULT_READER_MAX_TOKENS,
    PROBE_SYSTEM,
    LLMReader,
    Reader,
    reader_prompt_constants,
    system_prompt_for,
)
from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY

__all__ = [
    "DEFAULT_READER_MAX_TOKENS",
    "PROBE_SYSTEM",
    "READER_MODEL",
    "_ABSTRACTION_FRAGMENT",
    "_KNOWLEDGE_UPDATE_FRAGMENT",
    "_MULTI_SESSION_FRAGMENT",
    "_SYSTEM_PROMPT",
    "_TYPE_FRAGMENTS",
    "LLMReader",
    "MockReader",
    "Reader",
    "_parse_model_spec",
    "_resolve_provider",
    "build_reader",
    "reader_prompt_constants",
    "system_prompt_for",
]

# Pinned reader identity for the run (M5 #1525). The V2 runs were confounded
# by reader-model drift between runs (deepseek-chat → deepseek-v4-flash); the
# pin makes the code default equal what runs use. Any override is recorded
# (reader_pinned=false) + warned on stderr — never silent.
READER_MODEL = "openrouter:deepseek/deepseek-v4-flash"

# Provider priority for the READER when multiple keys are set — reuse the
# session-extraction order (sdk._SESSION_LLM_PROVIDER_PRIORITY).
_PROVIDER_PRIORITY = _SESSION_LLM_PROVIDER_PRIORITY

#: Model FAMILIES that reason by DEFAULT, matched on the model ID's last path
#: segment and INDEPENDENT of which endpoint serves it. The NEED to disable
#: reasoning is a property of the MODEL; the FIELD that does it is a property of
#: the ENDPOINT (``REASONING_CONTROL_BY_KEY_ENV``). Keying the whole guard on one
#: or the other alone is how it goes silently inert — a single-literal gate dies
#: on the next id rename (#4129), and an endpoint-keyed gate covers nothing on a
#: route that serves the same family over a different host (the pinned
#: ``READER_MODEL`` below is exactly that route). ``tortoise/analyze.py``'s #1790
#: gate is the in-repo precedent: it matches the FAMILY, on the last segment,
#: "so an OpenRouter route (deepseek/deepseek-v4-flash) is recognised".
REASONING_BY_DEFAULT_FAMILIES = frozenset({"deepseek"})

#: The control that turns that default reasoning OFF — keyed by the env var of
#: the endpoint that serves the call, because the request FIELD is the endpoint's:
#:
#: * ``api.deepseek.com`` documents ``thinking:{"type":"enabled"|"disabled"}``,
#:   and it is MEASURED (2026-10-25, on the one provider this box has a key for):
#:     judge  (official shape, max_tokens=10):  content='' finish=length,
#:             reasoning_tokens=10  -> label rule ``'yes' in ''`` grades EVERY
#:             question WRONG.
#:     reader (official shape, max_tokens=500): one 11,352-prompt-token
#:             multi-session question burned completion_tokens=500 with an EMPTY
#:             answer, was recorded ``valid: True`` with no error class, and
#:             scored 0 — a starved answer counted as a wrong one.
#:       both with ``thinking:{"type":"disabled"}`` -> 'yes' / a real answer,
#:       finish=stop, 1 completion token for the judge.
#: * OpenRouter has NO ``thinking`` field; its control is the ``reasoning``
#:   parameter, and this repo already sends ``reasoning:{"effort":"none"}`` on
#:   that route (``tortoise/model_adapters.py`` — the #1790 extractor lane's
#:   ``disable_reasoning``). Documented, but NOT measured from this box (no
#:   OpenRouter key here): if the route rejects it the call FAILS LOUDLY rather
#:   than scoring a starved answer as a wrong one, which is the fail-closed
#:   direction this guard exists for.
#:
#: A family in ``REASONING_BY_DEFAULT_FAMILIES`` served by an endpoint absent
#: from this map is the UNGUARDED case: ``build_reader``/``build_judge`` warn on
#: stderr and the report records that no control was sent (never silent).
REASONING_CONTROL_BY_KEY_ENV = {
    "DEEPSEEK_API_KEY": {"thinking": {"type": "disabled"}},
    "OPENROUTER_API_KEY": {"reasoning": {"effort": "none"}},
}


def _is_reasoning_by_default(model_id: str) -> bool:
    """True when ``model_id`` names a model of a reasoning-by-default FAMILY.

    Matched on the LAST path segment (``deepseek/deepseek-v4-flash`` is the same
    model as ``deepseek-v4-flash``) and on the family PREFIX, not on one literal
    id — #4129: a single-literal gate goes silently inert the moment the id is
    renamed, with no failing test and no log line.
    """
    tail = model_id.rsplit("/", 1)[-1]
    return any(tail == family or tail.startswith(family + "-")
               or tail.startswith(family + ".")
               for family in REASONING_BY_DEFAULT_FAMILIES)


def reasoning_control(model_id: str, key_env: str | None) -> dict:
    """The EXACT request addition that disables default reasoning, or ``{}``.

    Empty in two different cases, and the caller must not conflate them: the
    model does not reason by default (nothing to disable — the request stays
    byte-identical to the official shape), or it DOES and the endpoint serving
    it documents no control this repo knows (the guard cannot reach it — warn,
    and record it in the report, rather than claiming coverage).
    """
    if not _is_reasoning_by_default(model_id):
        return {}
    return dict(REASONING_CONTROL_BY_KEY_ENV.get(key_env or "", {}))


def reasoning_control_of(reader_or_judge: object | None) -> dict | None:
    """The reasoning-disable control the object's TRANSPORT actually sends.

    Read off the transport (``_model.reasoning_control`` — set by
    ``build_reader`` and by ``OfficialJudgeModel``) rather than restated by the
    caller, so a report's call-shape string cannot drift from the request that
    was made. ``None`` for an object with no transport (retrieval-only, the mock
    reader/judge). ONE home for both transports (#8486).
    """
    model = getattr(reader_or_judge, "_model", None)
    control = getattr(model, "reasoning_control", None)
    return dict(control) if isinstance(control, dict) else None


class _ReasoningControlTransport(OpenAICompatModel):
    """``OpenAICompatModel`` + the endpoint's reasoning-disable control.

    The ONLY difference from the official gen.py reader call shape: without it a
    reasoning-by-default model spends the whole completion budget on hidden
    reasoning and returns empty ``content``, and an empty reader answer is
    graded as a wrong answer — so the accuracy would measure the transport, not
    the retrieval or the reader (measured 2026-10-25; see
    ``REASONING_CONTROL_BY_KEY_ENV``). WHICH field carries the control is the
    endpoint's (``thinking`` on api.deepseek.com, ``reasoning`` on OpenRouter);
    the dict is passed in rather than hard-coded so the judge transport and this
    one cannot disagree about the route they are on.
    """

    def __init__(self, *args, control: dict | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        # The exact addition that WILL be sent — read back by the report so the
        # methodology cannot claim a shape this transport did not send.
        self.reasoning_control = dict(control or {})

    def build_request(self, system: str, user: str) -> dict:
        req = super().build_request(system, user)
        req.update(self.reasoning_control)
        return req


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
    fail-closed for the READER, which cannot do anything without a model.
    (Not a mirror of ``capture_session``, which since #3892 STORES the
    session's turns and skips only the extraction keylessly.)
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

    # Official reader call shape: temperature 0, bounded max_tokens, NO
    # response_format (JSON mode) — the answer is free text, and forcing
    # json_object mangles free-form answers on several providers.
    # #8486: the reasoning-disable control follows the FAMILY (a property of the
    # model) and is addressed in the FIELD the serving endpoint documents.
    control = reasoning_control(model_id, key_env)
    if control:
        model = _ReasoningControlTransport(
            id=model_id, base_url=base_url, api_key_env=key_env,
            response_format=None, max_tokens=DEFAULT_READER_MAX_TOKENS,
            control=control,
        )
    else:
        model = OpenAICompatModel(
            id=model_id, base_url=base_url, api_key_env=key_env,
            response_format=None, max_tokens=DEFAULT_READER_MAX_TOKENS,
        )
    # The report reads this back (``methodology.reader_call_shape``) so the shape
    # it states is the one that was sent — ``{}`` included, for a route this
    # guard cannot reach.
    model.reasoning_control = control
    if control:
        print(f"[longmem_eval] reader transport sends "
              f"{control}: this model FAMILY reasons by DEFAULT and would "
              f"otherwise spend the whole {DEFAULT_READER_MAX_TOKENS}-token "
              f"answer budget on hidden reasoning (measured 2026-10-25 — an "
              f"11,352-token prompt came back with completion_tokens=500 and "
              f"an EMPTY answer, recorded valid with no error class and "
              f"scored 0)", file=sys.stderr)
    elif _is_reasoning_by_default(model_id):
        # The family reasons by default, but this endpoint documents no control
        # this repo knows: the guard is ABSENT on this route. Say it — an empty
        # answer is scored as a wrong one and nothing downstream can tell them
        # apart (§8.1), so silence is the fail-open half of the defect.
        print(f"[longmem_eval] WARNING: reader model {model_id!r} is from a "
              f"REASONING-BY-DEFAULT family, but it is served by "
              f"{provider_name!r} ({key_env}), which documents no reasoning "
              f"control in REASONING_CONTROL_BY_KEY_ENV — this route has NO "
              f"starvation guard, so an empty answer here is scored as a "
              f"wrong one (§8.1). The report records the absence "
              f"(methodology.reader_call_shape).", file=sys.stderr)
    return LLMReader(model, model_id=model_id, model_spec=raw_spec,
                     provider=provider_name,
                     pinned=(raw_spec == READER_MODEL))
