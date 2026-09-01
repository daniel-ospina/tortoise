"""Production LLM model adapters + provider routing for the v2 extractor (#1530).

Ports the eval-proven adapters from ``tests/model_adapters.py`` (OpenRouterModel,
DeepSeekDirectModel, MODELS — #1350) into a production module, adds the
provider-routing layer (D2/D4/D5 of the #1530 plan) and the error-class
taxonomy that M2 (pre-flight) and M3 (retry/backoff) import. ``tests/
model_adapters.py`` is now a re-export shim so the eval harness keeps working
unchanged; production imports only this module (``test_no_tests_imports_in_
production.py`` stays green by construction).

Routing env vars (documented here; ``.env.example`` entries deferred to P4):
  - ``TORTOISE_EXTRACTOR_PROVIDER`` = ``deepseek-direct`` | ``openrouter`` —
    selects the PRIMARY; the other provider is the fallback when its key is
    configured. Unset infers DEEPSEEK first (owner-confirmed production
    decision: DeepSeek direct = primary extractor provider, OpenRouter =
    fallback — epic #1509 00-scope item 6). An explicit value whose key is
    absent fails closed with ValueError (never silently route elsewhere).
  - ``TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN`` seconds — process-local flap
    guard: a primary that failed transiently is skipped for this window
    (default 300; ``0`` disables). #1350 protection: without it every
    extraction pays a dead-primary attempt first during a sustained provider
    collapse. Process-local by design (per-worker); shared breakers are a
    follow-up if the collapse class recurs.
  - ``TORTOISE_EXTRACT_MODEL`` — canonical family-prefixed model id
    (default ``deepseek/deepseek-v4-flash``); the direct route sends the
    flash family's documented id (``deepseek-v4-flash``) with thinking
    explicitly disabled on the wire (pilot #1549 — ``deepseek-v4-flash``
    reasons by default and collapses to empty output; the legacy
    non-reasoning chat alias was retired upstream 2026-07-24 (still
    served during transition), #1790); the
    openrouter route sends the spec unchanged (D6).

Taxonomy contract (M2/M3 import these — do not fork):
  ``LlmErrorClass``, ``FATAL_STATUS_CODES``, ``FATAL_CONFIG_STATUS_CODES``,
  ``TRANSIENT_STATUS_CODES``, ``classify_llm_error``, ``is_transient``,
  ``is_fatal``.
"""
from __future__ import annotations

import contextlib
import enum
import errno
import os
import socket
import threading
import time
from urllib import error as urllib_error

import requests


class OpenRouterModel:
    """Adapter for the OpenRouter API — supports any model on the platform.

    Keyword-only ``complete(*, system, user)`` contract (the v2 pipeline calls
    it with keywords); ``max_tokens=None`` means UNCAPPED — the cap is omitted
    from the request body entirely (#1468: capped adapters truncate and
    silently lose chunks in the 5-stage extractor)."""
    provider = "openrouter"
    base_url = "https://openrouter.ai/api/v1/chat/completions"
    key_env = "OPENROUTER_API_KEY"

    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0,
                 thinking_budget: int = 0, disable_reasoning: bool = False,
                 json_mode: bool | None = None):
        self.id = model_id
        self.api_key = os.environ.get(self.key_env, "")
        self.max_tokens = max_tokens  # None = NO CAP (omit from request body)
        self.temperature = temperature
        self.thinking_budget = thinking_budget  # for reasoning models
        self.disable_reasoning = disable_reasoning  # send reasoning.effort=none
        # json_mode (#1987 Task 3): per-instance structural pin for the JSON
        # mode content-flip hazard (``_should_send_json_mode`` fires on the
        # substring "json" in user-controlled retrieved context — the ask
        # lane embeds retrieved memory, so a memory mentioning "json" would
        # send response_format=json_object on a free-text answer and mangle
        # it). None = unchanged behavior (the extraction lane); False = NEVER
        # send response_format; True = always send when the prompt requests
        # JSON.
        self.json_mode = json_mode
        # Session per adapter so a deadline can interrupt a hung read
        # (pilot #1549: the DeepSeek/OpenRouter API stalls mid-chunked-response;
        # requests.post per-call leaks the socket — close() on deadline kills it).
        self._session = requests.Session()
        # M3 (#1524, GATE-2): the per-call finish reason — "length" = the
        # generation hit the cap (truncation detected, not silently lost).
        self.last_finish_reason: str | None = None

    def close(self) -> None:
        """Interrupt any in-flight request (pilot #1549: called by the
        extractor's deadline when a call exceeds its wall-clock bound — the
        hung socket read raises and the daemon thread dies instead of
        leaking + billing forever)."""
        try:  # noqa: SIM105
            self._session.close()
        except Exception:
            pass

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        # Per-call override (M3 #1524): an explicit ``max_tokens`` beats the
        # constructor value — the v2 extractor's _complete passes its stage
        # caps here (race-free under --workers>1, unlike mutating
        # ``self.max_tokens``). None → the constructor default applies.
        cap = self.max_tokens if max_tokens is None else max_tokens
        if cap is not None:
            body["max_tokens"] = cap
        # Pilot #1549 (prompt-efficiency research): JSON mode — kills the
        # parse-error census class at zero prompt cost. DeepSeek requires the
        # prompt to contain "json" + an example (both present); OpenRouter
        # passes response_format through. Toggle: TORTOISE_JSON_MODE=0 disables.
        #
        # #1782: json_object mode is ONLY sent when the prompt actually
        # requests JSON — DeepSeek returns HTTP 400 if the mode is set but the
        # prompt doesn't contain the text "json" (case-insensitive substring).
        # Non-JSON calls (the preflight billing probe, ping, reader/judge
        # prompts) must NOT carry the mode. #1987 Task 3: the per-instance
        # ``json_mode=False`` pin (the ask lane) NEVER sends it — the
        # structural override beats the content heuristic.
        if self.json_mode is not False and _should_send_json_mode(system, user):
            body["response_format"] = {"type": "json_object"}
        # Enable thinking for reasoning models
        if self.thinking_budget > 0:
            body["reasoning"] = {"max_tokens": self.thinking_budget}
        elif self.disable_reasoning:
            body["reasoning"] = {"effort": "none"}

        r = self._session.post(
            self.base_url, headers=headers, json=body, timeout=(10, 60),
        )
        r.raise_for_status()
        data = r.json()

        # Extract usage for cost tracking
        usage = data.get('usage', {})
        self.last_prompt_tokens = usage.get('prompt_tokens', 0)
        self.last_completion_tokens = usage.get('completion_tokens', 0)
        self.last_cost = data.get('usage', {}).get('total_tokens', 0)  # will be overridden

        content = data['choices'][0]['message']['content']
        self.last_finish_reason = data["choices"][0].get("finish_reason")
        return content


class VeniceModel(OpenRouterModel):
    """Venice.ai adapter (pilot #1549 — 3-provider rotation). OpenAI-compatible
    at api.venice.ai/api/v1; serves deepseek-v4-flash at ~2x/4x cheaper than
    direct DeepSeek. Same model, different GPU farm — the rotation spreads the
    sustained load that degraded the direct API to 15-90s/call."""
    provider = "venice"
    base_url = "https://api.venice.ai/api/v1/chat/completions"
    key_env = "VENICE_API_KEY"

    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0, **kw):
        super().__init__(model_id, max_tokens=max_tokens,
                         temperature=temperature, **kw)
        self.api_key = os.environ.get(self.key_env, "")


class DeepSeekDirectModel(OpenRouterModel):
    """Direct DeepSeek API adapter (api.deepseek.com) — no OpenRouter hop.
    The flash family runs NON-reasoning: ``deepseek-v4-flash`` with thinking
    explicitly disabled (pilot #1549: api.deepseek.com's ``deepseek-v4-flash``
    reasons by default — thinking defaults ON — and collapses to empty
    output, 1500/1500 reasoning tokens, finish=length; the legacy
    alias that used to provide non-reasoning chat was retired upstream
    2026-07-24 (still served during transition), #1790);
    ``deepseek-v4-pro`` stays as-is (no
    collapse evidence — pending verification). Used when DEEPSEEK_API_KEY is
    set and TORTOISE_EXTRACTOR_PROVIDER != 'openrouter' (#1350 — the
    extractor's LLM calls were hitting OpenRouter connection errors under
    load; the direct API is the same model, different route)."""
    provider = "deepseek-direct"
    base_url = "https://api.deepseek.com/v1/chat/completions"
    key_env = "DEEPSEEK_API_KEY"

    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0, **kw):
        super().__init__(model_id, max_tokens=max_tokens,
                         temperature=temperature, **kw)
        self.api_key = os.environ.get(self.key_env, "")

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        cap = self.max_tokens if max_tokens is None else max_tokens
        if cap is not None:
            body["max_tokens"] = cap
        # #1746 (D6): JSON-mode parity on the DIRECT path — mirrors
        # OpenRouterModel (the pilot's direct route ran WITHOUT it — H1, the
        # untested lever). Toggle: TORTOISE_JSON_MODE=0 disables. DeepSeek's
        # "json"+example requirement is already satisfied by the S2/S4
        # prompts ("JSON object" + OUTPUT_CONTRACT example). NOTE: JSON mode
        # does NOT fix truncation (breaks at max_tokens) — the parse ladder
        # is the truncation pairing; no cap raise in #1746.
        #
        # #1782: gate on the prompt actually requesting JSON — DeepSeek 400s
        # when json_object mode is set without the text "json" in the prompt
        # (the preflight probe / ping prompts lack it). #1987 Task 3: the
        # per-instance ``json_mode=False`` pin (the ask lane) structurally
        # overrides the content heuristic.
        if self.json_mode is not False and _should_send_json_mode(system, user):
            body["response_format"] = {"type": "json_object"}
        # #1790: the flash family runs NON-reasoning. The legacy
        # alias (routed to v4-flash non-thinking) was retired upstream
        # 2026-07-24 (still served during transition); ``deepseek-v4-flash``
        # reasons by
        # DEFAULT (thinking: high) and collapses non-trivial prompts into
        # hidden reasoning tokens (pilot #1549: 1500/1500 reasoning tokens,
        # zero content). Disable thinking explicitly — the documented
        # OpenAI-format toggle (api-docs.deepseek.com/guides/thinking_mode;
        # live-verified 2026-08-28: zero reasoning_content, finish=stop,
        # byte-identical usage to the retired alias). ``deepseek-v4-pro``
        # keeps its default (no collapse evidence — pending verification).
        # prefix-agnostic: a provider-prefixed id (e.g. "deepseek/deepseek-v4-flash")
        # must not bypass the gate (gate ↔ MODELS drift would re-open #1549).
        if self.id.rsplit("/", 1)[-1] == "deepseek-v4-flash":
            body["thinking"] = {"type": "disabled"}
        r = self._session.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body, timeout=(10, 60),
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        self.last_finish_reason = data["choices"][0].get("finish_reason")
        return data["choices"][0]["message"]["content"]


# Pre-configured models (eval registry — kept name-stable: tests/model_adapters
# re-exports this; the eval harness and tools reference the names).
MODELS = {
    'deepseek-flash': lambda: OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=None, temperature=0.0),
    # Pilot #1549 (50-Q run, 2026-08-25) + #1790: the direct API id is the
    # NON-reasoning flash variant. api.deepseek.com's ``deepseek-v4-flash``
    # reasons by DEFAULT (thinking: high) and burns the whole max_tokens
    # budget on hidden reasoning tokens for non-trivial prompts (observed:
    # 1500/1500 reasoning tokens, finish_reason=length, ZERO output content)
    # — S1 then returns an empty story, S2/S4 never run, and extraction
    # silently produces no points. The legacy alias (routed to
    # v4-flash non-thinking) was retired upstream 2026-07-24
    # (still served during transition, #1790); the
    # replacement is the documented id ``deepseek-v4-flash``
    # WITH thinking explicitly disabled in ``DeepSeekDirectModel.complete``
    # (live-verified 2026-08-28: full story output, finish_reason=stop,
    # zero reasoning_content — byte-identical usage to the retired alias).
    'deepseek-flash-direct': lambda: DeepSeekDirectModel('deepseek-v4-flash', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro-direct': lambda: DeepSeekDirectModel('deepseek-v4-pro', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500),
    'deepseek-r1-xhigh': lambda: OpenRouterModel('deepseek/deepseek-r1-0528', max_tokens=500, thinking_budget=2000),
    'deepseek-v4-pro-xhigh': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=500, temperature=0.0),
    # OpenRouter-side only (epic #909 gate judges) — independent of Pi's qwen-tp provider config.
    # Reasoning models consume the shared max_tokens budget on internal reasoning; bound it
    # (thinking_budget) or disable it (disable_reasoning) so the label JSON actually gets emitted
    # (#946 gate runs observed all-reasoning/zero-content collapses at default settings).
    # ⚠️ judge_harness._apply_tuning OVERWRITES max_tokens with its CLI default (2000) — these
    # registry tunings are inert unless the CLI passes an explicit --max-tokens >= the value here
    # (the gate run used --max-tokens 12000 / 8000; a bare `--model qwen3.8-max` would starve).
    'qwen3.8-max': lambda: OpenRouterModel('qwen/qwen3.8-max', max_tokens=8000, temperature=0.0, thinking_budget=2000),
    'solar-pro4': lambda: OpenRouterModel('upstage/solar-pro4', max_tokens=None, temperature=0.0),
    'deepseek-v4-pro-noreason': lambda: OpenRouterModel('deepseek/deepseek-v4-pro', max_tokens=8000, temperature=0.0, disable_reasoning=True),
    'claude-opus-5': lambda: OpenRouterModel('anthropic/claude-opus-5', max_tokens=12000, temperature=0.0),
}


# ── Error-class taxonomy (the M2/M3 export contract — do not fork) ─────────

class LlmErrorClass(enum.Enum):
    FATAL = "fatal"                # deterministic, permanent — abort, no retry, no failover
    FATAL_CONFIG = "fatal_config"  # request/config shape bug — abort, no retry, no failover
    TRANSIENT = "transient"        # rate/network/server — retry (M3), failover-eligible (P2)
    UNKNOWN = "unknown"            # unclassified — treated as TRANSIENT-safe


FATAL_STATUS_CODES = frozenset({401, 402, 403})
FATAL_CONFIG_STATUS_CODES = frozenset({400, 404})  # provider-independent request-shape errors
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# errnos that identify an OSError as network-transport-level (the #1350
# collapse class: connection resets/refusals/route-down under provider load).
_NETWORK_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
    errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.EHOSTDOWN,
    errno.ETIMEDOUT, errno.ENOTCONN, errno.EPIPE,
})


def _http_status(exc: BaseException) -> int | None:
    """HTTP status carried by the exception (requests.HTTPError.response /
    urllib.error.HTTPError.code), or None when not an HTTP error."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        status = getattr(resp, "status_code", None)
        if isinstance(status, int):
            return status
    code = getattr(exc, "code", None)  # urllib.error.HTTPError
    if isinstance(code, int):
        return code
    return None


def _is_network_oserror(exc: BaseException) -> bool:
    """OSError with a network-transport errno (connection reset/refused/route
    down/timeout/pipe) — the transient-under-load class (#1350)."""
    return isinstance(exc, OSError) and getattr(exc, "errno", None) in _NETWORK_ERRNOS


def classify_llm_error(exc: BaseException) -> LlmErrorClass:
    """Classify an LLM-call exception per the #1530 taxonomy table.

    HTTP-classified first (401/402/403 FATAL; 400/404 FATAL_CONFIG; the
    408/425/429/5xx set TRANSIENT; any other 4xx FATAL_CONFIG — deterministic
    client error, never retry; any other 5xx TRANSIENT — server-side, may
    clear). Non-HTTP transport failures (requests.ConnectionError/Timeout,
    urllib.error.URLError, socket.timeout/TimeoutError, network OSError) are
    TRANSIENT. Anything else is UNKNOWN (treated as TRANSIENT-safe — one
    retry/failover permitted, M3 caps)."""
    status = _http_status(exc)
    if status is not None:
        if status in FATAL_STATUS_CODES:
            return LlmErrorClass.FATAL
        if status in FATAL_CONFIG_STATUS_CODES:
            return LlmErrorClass.FATAL_CONFIG
        if status in TRANSIENT_STATUS_CODES:
            return LlmErrorClass.TRANSIENT
        if 400 <= status < 500:
            # unknown 4xx = deterministic client error — "no flip-flop on 4xx"
            return LlmErrorClass.FATAL_CONFIG
        if status >= 500:
            return LlmErrorClass.TRANSIENT
        return LlmErrorClass.UNKNOWN

    if (isinstance(exc, (requests.ConnectionError, requests.Timeout,
                         urllib_error.URLError, socket.timeout,
                         TimeoutError))  # socket.timeout is TimeoutError (3.10+)
            or _is_network_oserror(exc)):
        return LlmErrorClass.TRANSIENT
    return LlmErrorClass.UNKNOWN


def is_transient(exc: BaseException) -> bool:
    """True → M3 may retry, P2 may fail over (TRANSIENT + UNKNOWN-safe)."""
    return classify_llm_error(exc) in (LlmErrorClass.TRANSIENT, LlmErrorClass.UNKNOWN)


def is_fatal(exc: BaseException) -> bool:
    """True → M3 aborts immediately, P2 never fails over (FATAL + FATAL_CONFIG)."""
    return classify_llm_error(exc) in (LlmErrorClass.FATAL, LlmErrorClass.FATAL_CONFIG)


def is_billing_exhausted(exc: BaseException) -> bool:
    """True → HTTP 402 (Payment Required) — the provider's credits ran out.

    The one 'fatal' class that is PROVIDER-specific (a balance emptied
    mid-run), not config-inherent: auth (401/403) means the credential is
    wrong everywhere, config 4xx means the request shape is wrong everywhere
    — rotation would just retry the same bug. A 402 is a runtime condition
    of THAT provider; ``RotatingModel`` cooldowns it and rotates to an
    alternative so the run continues (#1951). Deliberately NOT part of the
    M2/M3 taxonomy export contract — ``is_fatal``/``classify_llm_error``
    semantics are unchanged for the retry/abort consumers (run.py M3,
    extractor_v2); only the rotation pool consults this hook."""
    return _http_status(exc) == 402


# ── Provider routing (D2) ──────────────────────────────────────────────────

_PROVIDER_NAMES = ("deepseek-direct", "openrouter")


def resolve_extractor_provider() -> tuple[str, str | None]:
    """Resolve (primary, fallback) from TORTOISE_EXTRACTOR_PROVIDER + keys.

    D2 table: the env var selects the PRIMARY; the other provider is the
    fallback when its key is configured. An explicit value whose key is
    absent fails closed with ValueError (never silently route elsewhere);
    an invalid value lists the valid ones. Unset infers DEEPSEEK first
    (owner-confirmed production decision); no keys → (None, None) — the
    caller gate fails closed as today."""
    explicit = os.environ.get("TORTOISE_EXTRACTOR_PROVIDER", "").strip().lower()
    ds_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    or_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    vz_key = bool(os.environ.get("VENICE_API_KEY"))

    # Pilot #1549 — 3-provider rotation: the fallback chain extends to
    # venice when its key is configured. The ordered pool is
    # [primary, openrouter?, venice?] — build_extractor_model rotates.
    def _chain(primary: str, *ordered: str) -> tuple[str, list[str]]:
        avail = [primary]
        for p in ordered:
            if ((p == "deepseek-direct" and ds_key)
                    or (p == "openrouter" and or_key)
                    or (p == "venice" and vz_key)):
                avail.append(p)
        return (primary, avail)

    if explicit == "deepseek-direct":
        if not ds_key:
            raise ValueError(
                "TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct but "
                "DEEPSEEK_API_KEY is not set — an explicit provider names a "
                "key that isn't configured; never silently routing elsewhere "
                "(#1530 fail-closed)")
        return _chain("deepseek-direct", "openrouter", "venice")
    if explicit == "openrouter":
        if not or_key:
            raise ValueError(
                "TORTOISE_EXTRACTOR_PROVIDER=openrouter but "
                "OPENROUTER_API_KEY is not set — an explicit provider names a "
                "key that isn't configured; never silently routing elsewhere "
                "(#1530 fail-closed)")
        return _chain("openrouter", "deepseek-direct", "venice")
    if explicit:
        raise ValueError(
            f"TORTOISE_EXTRACTOR_PROVIDER={explicit!r} invalid — valid values: "
            f"{' | '.join(_PROVIDER_NAMES)}")
    if ds_key:
        return _chain("deepseek-direct", "openrouter", "venice")
    if or_key:
        return _chain("openrouter", "deepseek-direct", "venice")
    if vz_key:
        return _chain("venice", "openrouter", "deepseek-direct")
    return (None, None)


# ── Ask-lane provider-capability routing (#2069) ───────────────────────────
# The ask lane's reader routing is provider-capability-aware (spec declares
# the family): a spec valid only on OpenRouter (``qwen/qwen3.8-max``) must
# NEVER be posted to the deepseek-direct primary (HTTP 400 → correctly
# FATAL_CONFIG → 502 — the #2069 defect). The family prefix decides the
# servable set; ``resolve_reader_provider`` intersects it with the
# env/keys-resolved order and honors ``TORTOISE_ASK_PROVIDER``.

#: Family prefix → providers that can serve the family. OpenRouter-only
#: families (qwen/, upstage/, anthropic/, … — the MODELS registry's
#: judge/reader-only families); ``deepseek`` (prefixed or bare) keeps the
#: deepseek-direct/openrouter/venice pool. An UNKNOWN family must fail loud
#: — never "→ all" (a typo'd/future family must not fall through to
#: deepseek-direct and re-introduce the 400-on-foreign-spec defect).
_SPEC_FAMILY_PROVIDERS = {
    "qwen": {"openrouter"},
    "upstage": {"openrouter"},
    "anthropic": {"openrouter"},
    "deepseek": {"deepseek-direct", "openrouter", "venice"},
}

#: Ask-lane provider enum (``TORTOISE_ASK_PROVIDER`` valid values).
_ASK_PROVIDERS = ("deepseek-direct", "openrouter", "venice")

#: Provider → key env var (the empty-intersection guard names these).
_PROVIDER_KEY_ENV = {
    "deepseek-direct": "DEEPSEEK_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "venice": "VENICE_API_KEY",
}

#: Ordered provider chain per explicit primary (mirrors
#: ``resolve_extractor_provider``'s ``_chain`` ordering).
_ASK_PROVIDER_CHAIN = {
    "deepseek-direct": ("deepseek-direct", "openrouter", "venice"),
    "openrouter": ("openrouter", "deepseek-direct", "venice"),
    "venice": ("venice", "openrouter", "deepseek-direct"),
}


def _providers_can_serve(model_id: str) -> set[str]:
    """Provider-capability map (#2069): which providers can serve a spec.

    Family = the spec's prefix (``qwen/``, ``upstage/``, ``anthropic/`` →
    OpenRouter-only; ``deepseek/`` + bare deepseek ids → the
    deepseek-direct/openrouter/venice pool). An UNKNOWN family prefix fails
    LOUD with ``ValueError`` naming the family (never "→ all") — an
    unrecognized family must never fall through to deepseek-direct and
    re-introduce the defect (deepseek 400 on a foreign spec). The ask lane
    normalizes MODELS keys (``_ASK_MODELS_KEY_SPECS``) and rejects the
    eval's colon-form BEFORE this runs, so a bare ``qwen3.8-max`` or
    ``openrouter:qwen/qwen3.8-max`` never reaches the unknown-family branch.
    """
    family, sep, _rest = model_id.partition("/")
    if not sep:
        # bare id — deepseek back-compat; a bare non-deepseek id (post
        # ``_ASK_MODELS_KEY_SPECS`` normalization) fails loud.
        if not family.startswith("deepseek"):
            raise ValueError(
                f"unknown model family for bare {model_id!r} — bare "
                f"non-deepseek ids must use the family-prefixed form "
                f"(e.g. 'qwen/qwen3.8-max', 'upstage/solar-pro4', "
                f"'anthropic/claude-opus-5')")
        family = "deepseek"
    servable = _SPEC_FAMILY_PROVIDERS.get(family)
    if servable is None:
        raise ValueError(
            f"unknown model family {family!r} in {model_id!r} — no provider "
            f"can serve it; recognized families: "
            f"{', '.join(sorted(_SPEC_FAMILY_PROVIDERS))}")
    return servable


def resolve_reader_provider(model_id: str) -> tuple[str | None, list[str]]:
    """Resolve (primary, ordered pool) for the ASK lane (#2069).

    The servable set comes from ``_providers_can_serve(model_id)`` (the
    family capability map); the env/keys-resolved order is INTERSECTED with
    it. ``TORTOISE_ASK_PROVIDER`` (default ``auto``) selects the primary;
    an explicit value without its key fails closed with ``ValueError``
    (mirror ``resolve_extractor_provider``). With all three provider keys
    set, the shared builder returns the pilot #1549 ``RotatingModel`` whose
    deterministic reorder picks the primary — the explicit provider still
    gates the SERVABLE set (fail-closed when unkeyed) but the rotation
    order wins the primary slot (pre-existing extraction-lane semantics,
    byte-identical). An EMPTY intersection raises a
    build-time ``ValueError`` naming the missing key — fail-fast, NEVER a
    silent misbuild that 401s at call time. The deepseek family preserves
    the extraction lane's lenient no-key default (a single OpenRouter
    adapter) so no-key ask behavior is unchanged.
    """
    explicit = (os.environ.get("TORTOISE_ASK_PROVIDER", "").strip().lower()
                or "auto")
    ds_key = bool(os.environ.get("DEEPSEEK_API_KEY"))
    or_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    vz_key = bool(os.environ.get("VENICE_API_KEY"))
    keyed = {"deepseek-direct": ds_key, "openrouter": or_key,
             "venice": vz_key}
    servable = _providers_can_serve(model_id)

    if explicit != "auto":
        if explicit not in _ASK_PROVIDERS:
            raise ValueError(
                f"TORTOISE_ASK_PROVIDER={explicit!r} invalid — valid values: "
                f"auto | {' | '.join(_ASK_PROVIDERS)}")
        if explicit not in servable:
            raise ValueError(
                f"TORTOISE_ASK_PROVIDER={explicit} cannot serve {model_id!r} "
                f"— the {model_id.split('/', 1)[0]} family is served only by "
                f"{', '.join(sorted(servable))}")
        if not keyed[explicit]:
            raise ValueError(
                f"TORTOISE_ASK_PROVIDER={explicit} but "
                f"{_PROVIDER_KEY_ENV[explicit]} is not set — an explicit "
                f"provider names a key that isn't configured; never "
                f"silently routing elsewhere (#1530 fail-closed)")
        chain = _ASK_PROVIDER_CHAIN[explicit]
        pool = [p for p in chain if p in servable and keyed[p]]
        return (explicit, pool)

    # auto mode: ordered servable providers that have keys configured.
    pool = [p for p in _ASK_PROVIDER_CHAIN["deepseek-direct"]
            if p in servable and keyed[p]]
    if pool:
        return (pool[0], pool)
    # Empty intersection. The deepseek family keeps the lenient no-key
    # default (a single OpenRouter adapter — no-key ask behavior unchanged);
    # every other family fails LOUD at build time naming the key it needs.
    if "deepseek-direct" in servable and not any(keyed.values()):
        return (None, ["openrouter"])
    missing = sorted({_PROVIDER_KEY_ENV[p] for p in servable
                      if not keyed[p]})
    raise ValueError(
        f"no configured provider can serve {model_id!r}: the "
        f"{', '.join(sorted(servable))} lane(s) need "
        f"{' or '.join(missing)} in the environment")


def _build_routing_model(model_id: str, pool_names: list[str], *,
                         max_tokens, temperature, json_mode):
    """Shared private pool-builder (#2069) — both lanes' RoutingModel /
    RotatingModel construction (extraction behavior byte-identical)."""
    providers = [_build_single(p, model_id, max_tokens=max_tokens,
                               temperature=temperature, json_mode=json_mode)
                 for p in pool_names]
    # Pilot #1549: 3+ configured providers → the rotating pool (spread the
    # sustained load + redundancy); 1-2 → the existing RoutingModel semantics.
    if len(providers) >= 3:
        # Scale-optimized weights (pilot #1549 research): order the pool
        # Venice-first (1000 RPM backbone), OpenRouter (cheapest lane), then
        # DeepSeek direct as the small spare (expensive + starves under load).
        by_name = {p.provider: p for p in providers}
        ordered = [by_name.get(name) for name in
                   ("venice", "openrouter", "deepseek-direct")]
        ordered = [p for p in ordered if p is not None]
        weights = {"venice": 0.50, "openrouter": 0.35, "deepseek-direct": 0.15}
        w = [weights.get(p.provider, 0.33) for p in ordered]
        return RotatingModel(ordered, cooldown_s=_failover_cooldown_seconds(),
                             weights=w, model=model_id)
    return RoutingModel(providers[0], providers[1] if len(providers) > 1 else None,
                        cooldown_s=_failover_cooldown_seconds())


# ── In-process failover cooldown (D5 flap guard) ───────────────────────────

_FAILOVER_COOLDOWN: dict[str, float] = {}  # provider -> last transient failure ts
_FAILOVER_LOCK = threading.Lock()


def _failover_cooldown_seconds() -> float:
    raw = os.environ.get("TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN", "").strip()
    if not raw:
        return 300.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 300.0


def _note_failure(provider: str, cooldown_s: float) -> None:
    if cooldown_s <= 0:
        return
    with _FAILOVER_LOCK:
        _FAILOVER_COOLDOWN[provider] = time.time()


def _primary_in_cooldown(provider: str, cooldown_s: float) -> bool:
    if cooldown_s <= 0:
        return False
    with _FAILOVER_LOCK:
        ts = _FAILOVER_COOLDOWN.get(provider)
    if ts is None:
        return False
    return (time.time() - ts) < cooldown_s


def _reset_failover_cooldown() -> None:
    """Test seam — clears the process-local flap state."""
    with _FAILOVER_LOCK:
        _FAILOVER_COOLDOWN.clear()


# ── RoutingModel (D4/D5) ───────────────────────────────────────────────────

class RoutingModel:
    """Primary adapter + optional fallback with failover (D4/D5 #1530).

    ``complete()`` tries the primary; the exception class decides (D4):
    FATAL (401/402/403) and FATAL_CONFIG (400/404/unknown 4xx) re-raise
    immediately — no retry, NO failover; TRANSIENT/UNKNOWN fails over to the
    fallback when configured. Stickiness (D5): once a call fails over,
    ``last_route``/``route`` flip to the fallback and STAY there for the rest
    of this extraction (forward-only, never back mid-extraction). A primary
    in the process-local cooldown window is skipped outright (fallback used
    directly) — the #1350 flap protection.

    Exposes the capture meta contract (D8): ``provider`` (the configured
    primary), ``route``/``last_route`` (the active / most-recent route),
    ``failover_used``, ``errors`` (classified exception traces)."""
    def __init__(self, primary, fallback=None, *, cooldown_s: float = 300.0):
        self.primary = primary
        self.fallback = fallback
        self.cooldown_s = cooldown_s
        self.provider = primary.provider
        self.route = primary.provider     # active route (flips on failover, forward-only)
        self.last_route: str | None = None  # route of the most recent call
        self.failover_used = False
        self.errors: list[str] = []
        self._failed_over = False
        # M3 (#1524, GATE-2): surfaced from the inner adapter after each call.
        self.last_finish_reason: str | None = None
        # #1987 Task 3: the RESOLVED SPEC (the wire id the serving adapter
        # carries — ``_direct_wire_id`` strips the family prefix on the direct
        # lane; the full spec on the OpenRouter lane) + per-call usage
        # forwards, mirrored from the serving adapter after each call (same
        # pattern as ``last_finish_reason``). The ask response's ``model``
        # field and per-call token capture read these.
        self.model: str = getattr(primary, "id", "")
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        if self.fallback is not None and (
                self._failed_over
                or _primary_in_cooldown(self.primary.provider, self.cooldown_s)):
            return self._call(self.fallback, system, user, failover=True,
                              max_tokens=max_tokens)
        try:
            return self._call(self.primary, system, user, failover=False,
                              max_tokens=max_tokens)
        except BaseException as e:  # noqa: BLE001, RUF100 — classify first
            self.errors.append(f"{type(e).__name__}: {e}")
            if is_fatal(e) or self.fallback is None:
                raise
            _note_failure(self.primary.provider, self.cooldown_s)
            self._failed_over = True
            return self._call(self.fallback, system, user, failover=True,
                              max_tokens=max_tokens)

    def _call(self, adapter, system: str, user: str, *, failover: bool,
              max_tokens: int | None = None) -> str:
        out = adapter.complete(system=system, user=user,
                               max_tokens=max_tokens)
        self.last_route = adapter.provider
        self.last_finish_reason = getattr(adapter, "last_finish_reason", None)
        # #1987 Task 3: per-call usage + resolved-spec forwards.
        self.last_prompt_tokens = getattr(adapter, "last_prompt_tokens", 0)
        self.last_completion_tokens = getattr(adapter, "last_completion_tokens", 0)
        self.model = getattr(adapter, "id", self.model)
        if failover:
            self.route = adapter.provider
            self.failover_used = True
        return out

    def close(self) -> None:
        """Close the inner adapters (deadline interrupt — mirrors
        RotatingModel.close; RoutingModel was missed by the #1655 fix)."""
        for adapter in (self.primary, self.fallback):
            if adapter is None:
                continue
            close = getattr(adapter, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    close()


def _prompt_requests_json(system: str | None, user: str | None) -> bool:
    """True when a prompt asks for JSON output (the S2/S4 extractor prompts
    say "JSON object" + the OUTPUT_CONTRACT example).

    A bare substring match — "json" appearing anywhere in the combined
    system+user text, case-insensitive (matches "JSON", "non-json",
    "JSONL", "jsonify", ...). #1782: DeepSeek returns HTTP 400 when
    "response_format": {"type": "json_object"} is sent but the prompt
    lacks the text "json". Non-JSON calls — the preflight billing probe,
    ping, reader/judge prompts — must NOT carry the mode. This is the
    documented DeepSeek contract, not a heuristic: json_object mode requires
    the model to see "json" in the prompt to know the expected output
    shape.
    """
    hay = f"{system or ''} {user or ''}".lower()
    return "json" in hay


def _should_send_json_mode(system: str | None, user: str | None) -> bool:
    """Single-source gate for ``response_format: {"type": "json_object"}``
    (#1782) — shared by OpenRouterModel.complete and
    DeepSeekDirectModel.complete so the two bodies can never drift.

    True only when TORTOISE_JSON_MODE is enabled (default "1", read per
    call — the toggle can flip mid-run) AND the prompt requests JSON
    (delegated to ``_prompt_requests_json``)."""
    return (os.environ.get("TORTOISE_JSON_MODE", "1") == "1"
            and _prompt_requests_json(system, user))


def _strip_family_prefix(model_id: str) -> str:
    """Direct-route wire normalization (D6): ``deepseek/deepseek-v4-flash`` →
    ``deepseek-v4-flash``. Intermediate step feeding ``_direct_wire_id`` (the
    direct lane's bare-id normalization)."""
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


def _direct_wire_id(model_id: str) -> str:
    """Direct-API wire id (D6 + #1790): the direct lane sends BARE ids —
    ``deepseek/deepseek-v4-flash`` → ``deepseek-v4-flash`` (the OpenRouter
    lane keeps the family prefix). The flash family keeps its current
    documented id ``deepseek-v4-flash``; non-reasoning behavior is achieved
    by explicitly disabling thinking in ``DeepSeekDirectModel.complete``
    (the legacy alias — routing to v4-flash non-thinking — was
    retired upstream 2026-07-24 (still served during transition),
    #1790). ``deepseek-v4-pro`` is
    unchanged (no collapse evidence — out of scope pending verification)."""
    return _strip_family_prefix(model_id)


def _build_single(provider: str, model_id: str, *, max_tokens, temperature,
                 json_mode: bool | None = None):
    if provider == "deepseek-direct":
        return DeepSeekDirectModel(
            _direct_wire_id(model_id),
            max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode)
    if provider == "venice":
        # Venice's catalog serves the documented flash id (docstring). A
        # wrong id fails LOUD via preflight (config-4xx → fatal gate), never
        # silently; verify the venice catalog before enabling the pool
        # (#1549).
        return VeniceModel(
            _strip_family_prefix(model_id),
            max_tokens=max_tokens, temperature=temperature,
            json_mode=json_mode)
    return OpenRouterModel(model_id, max_tokens=max_tokens,
                           temperature=temperature, json_mode=json_mode)


class RotatingModel:
    """3-provider pool with round-robin rotation + per-provider cooldown
    (pilot #1549 — the DeepSeek direct API degrades to 15-90s/call under
    sustained load; three independent GPU farms with uncorrelated
    reliability spread the load AND give redundancy).

    ``complete()`` routes each call to the next healthy provider in the
    rotation; a transient failure puts that provider in cooldown (skipped
    for ``cooldown_s``) and the next provider is tried. Auth (401/403) and
    config 4xx (P2 taxonomy) re-raise immediately — no rotation on
    credential/request-shape bugs. HTTP 402 (billing exhausted) is
    rotation-eligible (#1951): cooldown THAT provider and continue on an
    alternative — the run proceeds slower, not dead — raising only when
    there is no alternative provider (fail loud, no infinite loop). Exposes
    the capture-meta contract:
    ``provider``/``route`` (active provider), ``errors``, ``last_finish_reason``
    (the truncation signal — read from the serving adapter), ``close()``
    (interrupt a hung read — the #1655 fix, applied to the active adapter)."""
    def __init__(self, providers: list, *, cooldown_s: float = 300.0,
                 weights: list[float] | None = None, model: str | None = None):
        self.providers = providers
        self.cooldown_s = cooldown_s
        # Pilot #1549 (scale research): weighted rotation — each provider's
        # share of traffic ∝ its weight. Venice 1000 RPM = throughput
        # backbone; OpenRouter = cheapest lane ($0.056/$0.112 vs $0.14+);
        # DeepSeek direct = expensive ($0.22/$0.66) + starves under load →
        # keep it a small spare. Weights default to equal round-robin.
        self.weights = weights or [1.0 / len(providers)] * len(providers)
        self._rr = 0
        self._cooldowns: dict[str, float] = {}
        self.errors: list[str] = []
        self.last_finish_reason: str | None = None
        self.route = providers[0].provider if providers else None
        # #1987 Task 3: the resolved-spec + per-call usage forwards (mirrored
        # from the serving adapter; ``model`` is the serving lane's wire id —
        # NOT the raw model_id spec, so RoutingModel and RotatingModel report
        # the SAME format).
        self.model: str = (getattr(providers[0], "id", "")
                           if providers else (model or ""))
        self.last_prompt_tokens: int = 0
        self.last_completion_tokens: int = 0

    @property
    def provider(self) -> str:
        return self.route or (self.providers[0].provider if self.providers else "none")

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        import time
        now = time.time()
        n = len(self.providers)
        if n == 0:
            raise RuntimeError("RotatingModel with no providers")
        last_err: Exception | None = None
        for _ in range(n * 3):  # bounded attempts: each provider at most ~3x per call
            idx = self._pick()
            p = self.providers[idx]
            if self._cooldowns.get(p.provider, 0.0) > now:
                continue
            try:
                out = p.complete(system=system, user=user, max_tokens=max_tokens)
                self.route = p.provider
                self.last_finish_reason = getattr(p, "last_finish_reason", None)
                # #1987 Task 3: per-call usage + resolved-spec forwards.
                self.last_prompt_tokens = getattr(p, "last_prompt_tokens", 0)
                self.last_completion_tokens = getattr(p, "last_completion_tokens", 0)
                self.model = getattr(p, "id", self.model)
                return out
            except Exception as e:
                last_err = e
                billing = is_billing_exhausted(e)
                if is_fatal(e) and not billing:
                    raise  # auth (401/403) + config 4xx — never rotate (#1951)
                if billing and n == 1:
                    raise  # no alternative provider — fail loud, no infinite loop
                self._cooldowns[p.provider] = now + self.cooldown_s
                self.errors.append(f"{p.provider}: {type(e).__name__}: {e}")
        raise last_err if last_err is not None else RuntimeError(
            f"all {n} providers in cooldown")

    def _pick(self) -> int:
        """Weighted round-robin: pick the next provider by cumulative weight
        from the advancing cursor (deterministic, proportional to weights)."""
        import random
        total = sum(self.weights)
        r = (self._rr + random.random()) % total  # advance by a random offset
        self._rr = (self._rr + 1) % total
        acc = 0.0
        for i, w in enumerate(self.weights):
            acc += w
            if r < acc:
                return i
        return 0

    def close(self) -> None:
        for p in self.providers:
            close = getattr(p, "close", None)
            if close is not None:
                try:  # noqa: SIM105
                    close()
                except Exception:
                    pass


# Eval CLI extractor-registry keys (MODELS) → the real DeepSeek/OpenRouter
# model ids. The eval passes 'deepseek-flash-direct' etc. (the legacy run.py
# MODELS keys); build_extractor_model treats model_id as a raw API spec, so
# extractor registry keys must be normalized here — otherwise the suffix
# reaches the API as the model name (pilot #1549: HTTP 400 'you passed
# deepseek-flash-direct' on every S1 call → zero extraction). Only the
# extractor-relevant DeepSeek keys are mapped; other MODELS keys (qwen3.8-max,
# solar-pro4, claude-opus-5, ...) are judge/reader-only and intentionally
# pass through — they are not valid extractor specs on either provider.
_REGISTRY_KEY_TO_ID = {
    "deepseek-flash": "deepseek/deepseek-v4-flash",
    # Pilot #1549 (2026-08-25) + #1790: the DIRECT-API flash wire id is the
    # current documented id ``deepseek-v4-flash``; non-reasoning behavior is
    # achieved by disabling thinking explicitly in ``DeepSeekDirectModel.complete``
    # (the legacy alias was retired upstream 2026-07-24 (still served
    # during transition), #1790 — v4-flash reasons by default and burns
    # the max_tokens budget on
    # hidden reasoning for non-trivial prompts: 1500/1500 reasoning tokens
    # observed, finish_reason=length, ZERO content — S1 collapses to an empty
    # story and extraction silently produces no points). The key maps to the
    # FAMILY-PREFIXED id so every route gets a valid wire id: the direct lane
    # strips to the bare flash id (``_direct_wire_id``), the OpenRouter lane
    # keeps the valid prefixed id, and the venice lane serves its documented
    # catalog id. ``deepseek-v4-pro-direct`` is unchanged (no collapse
    # evidence for v4-pro — pending verification). The v4-pro keys are ALSO
    # family-prefixed so the OpenRouter pool lane gets a valid id (bare ids
    # 404 there → fatal → pool-kill, #1549 class).
    "deepseek-flash-direct": "deepseek/deepseek-v4-flash",
    "deepseek-v4-pro-direct": "deepseek/deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "deepseek-v4-pro-noreason": "deepseek/deepseek-v4-pro",
    "deepseek-r1-xhigh": "deepseek/deepseek-r1-0528",
    "deepseek-v4-pro-xhigh": "deepseek/deepseek-v4-pro",
}


# Public alias — the eval harness (tools/longmem_eval/run.py) imports the
# registry-key remap as a stable contract; keep this name public.
REGISTRY_KEY_TO_ID = _REGISTRY_KEY_TO_ID


# #2069: ASK-lane MODELS-key normalization — bare non-deepseek MODELS
# registry keys (qwen3.8-max, solar-pro4, claude-opus-5 — passed through
# unmapped by ``_REGISTRY_KEY_TO_ID`` above, which maps deepseek-* keys
# only) are NOT deepseek-family and must never route to deepseek-direct.
# Resolve BEFORE the family parse: the bare key → its family-prefixed
# spec (derived from the MODELS registry's target slugs — verified:
# qwen3.8-max → qwen/qwen3.8-max, solar-pro4 → upstage/solar-pro4,
# claude-opus-5 → anthropic/claude-opus-5). A bare key absent from this
# map with a non-deepseek name fails loud in ``_providers_can_serve``.
_ASK_MODELS_KEY_SPECS = {
    "qwen3.8-max": "qwen/qwen3.8-max",
    "solar-pro4": "upstage/solar-pro4",
    "claude-opus-5": "anthropic/claude-opus-5",
}


def _normalize_ask_model_spec(model_id: str) -> str:
    """Ask-lane spec normalization (#2069) — runs BEFORE the family parse:
    (1) the eval registry-key remap (``_REGISTRY_KEY_TO_ID``, deepseek keys
    only — back-compat); (2) colon-form REJECTION — the eval lane's
    ``provider:model`` format (the documented ``TORTOISE_LME_READER_MODEL``
    value) is NOT the product format, and ``openrouter:qwen/qwen3.8-max``
    must not fall into unknown-prefix handling (it would 400 on
    deepseek-direct → 502); the ask lane accepts ``family/model`` ONLY and
    raises pointing at the family-prefixed form; (3) bare MODELS keys via
    ``_ASK_MODELS_KEY_SPECS`` so a bare non-deepseek registry key never
    routes to deepseek-direct."""
    model_id = _REGISTRY_KEY_TO_ID.get(model_id, model_id)
    if ":" in model_id:
        raise ValueError(
            f"ask-lane model spec {model_id!r} uses the eval's colon-form "
            f"('provider:model') — the ask lane accepts the family-prefixed "
            f"product form only (e.g. 'qwen/qwen3.8-max'); drop the provider "
            f"prefix")
    if "/" not in model_id:
        model_id = _ASK_MODELS_KEY_SPECS.get(model_id, model_id)
    return model_id


def build_extractor_model(model_id: str | None = None, *,
                          max_tokens: int | None = 4000,
                          temperature: float = 0.0,
                          json_mode: bool | None = None) -> RoutingModel:
    """Production entry — build the routing extractor model (D7).

    Resolves (primary, fallback) via ``resolve_extractor_provider()`` and
    wraps them in a ``RoutingModel``. ``model_id`` defaults to
    ``TORTOISE_EXTRACT_MODEL`` (or ``deepseek/deepseek-v4-flash``; the
    default's direct route sends ``deepseek-v4-flash`` with thinking
    disabled on the wire — pilot #1549/#1790). Builds
    leniently — with NO keys at all it degrades to a single OpenRouter
    adapter (back-compat for direct callers / ``TestModelAdapterBounds``);
    fail-closed is enforced at the pipeline gates, not here. An explicit
    ``TORTOISE_EXTRACTOR_PROVIDER`` whose key is absent raises ValueError
    (config error, everywhere).

    ``json_mode`` (#1987 Task 3): threaded to every built adapter — None
    keeps the extraction lane's content-heuristic behavior unchanged;
    False structurally disables ``response_format`` on the ask lane;
    True always sends it when the prompt requests JSON.
    """
    if model_id is None:
        model_id = (os.environ.get("TORTOISE_EXTRACT_MODEL", "").strip()
                    or "deepseek/deepseek-v4-flash")
    # Registry-key normalization (pilot #1549 fix) — unknown strings pass
    # through untouched (raw specs stay valid).
    model_id = _REGISTRY_KEY_TO_ID.get(model_id, model_id)
    primary_name, pool_names = resolve_extractor_provider()  # noqa: RUF059
    if not pool_names:
        pool_names = ["openrouter"]  # lenient no-key default (D3)
    return _build_routing_model(model_id, pool_names, max_tokens=max_tokens,
                                temperature=temperature, json_mode=json_mode)


def build_reader_model(model_id: str | None = None, *,
                       max_tokens: int = 500,
                       temperature: float = 0.0) -> RoutingModel:
    """Build the ask-lane reader model (#1987 Task 3, #2069).

    ``model_id=None`` resolves ``TORTOISE_ASK_MODEL`` (mirroring
    ``build_extractor_model``'s ``TORTOISE_EXTRACT_MODEL`` fallback),
    hardcoded ``deepseek/deepseek-v4-flash`` as the final fallback.
    The ask lane pins ``json_mode=False`` structurally — retrieved memory
    can mention "json" and ``_should_send_json_mode`` must never fire
    ``response_format`` on a free-text answer (the eval lane was immune via
    ``response_format=None``).

    #2069 provider-capability routing: the pool is built ONLY from
    providers that can serve the spec's family (``resolve_reader_provider``
    — ``qwen/qwen3.8-max`` → OpenRouter-only; the deepseek-direct primary
    is structurally ABSENT from non-deepseek pools, so a deepseek-direct
    "400 on a foreign spec" is impossible). ``TORTOISE_ASK_PROVIDER``
    (default ``auto``) selects the primary; an empty intersection (e.g. a
    qwen spec with no ``OPENROUTER_API_KEY``) raises a build-time
    ``ValueError`` naming the missing key. Official reader call shape:
    temperature 0, bounded ``max_tokens`` (default 500).
    """
    if model_id is None:
        model_id = (os.environ.get("TORTOISE_ASK_MODEL", "").strip()
                    or "deepseek/deepseek-v4-flash")
    # #2069: registry-key remap + MODELS-key normalization + colon-form
    # rejection BEFORE the family parse (a bare non-deepseek MODELS key or
    # the eval's ``provider:model`` colon-form must never reach
    # ``_providers_can_serve``'s unknown-family branch).
    model_id = _normalize_ask_model_spec(model_id)
    _primary_name, pool_names = resolve_reader_provider(model_id)
    return _build_routing_model(model_id, pool_names, max_tokens=max_tokens,
                                temperature=temperature, json_mode=False)
