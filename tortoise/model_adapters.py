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
    (default ``deepseek/deepseek-v4-flash``); the direct route sends the bare
    id on the wire, the openrouter route sends it unchanged (D6).

Taxonomy contract (M2/M3 import these — do not fork):
  ``LlmErrorClass``, ``FATAL_STATUS_CODES``, ``FATAL_CONFIG_STATUS_CODES``,
  ``TRANSIENT_STATUS_CODES``, ``classify_llm_error``, ``is_transient``,
  ``is_fatal``.
"""
from __future__ import annotations

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
                 thinking_budget: int = 0, disable_reasoning: bool = False):
        self.id = model_id
        self.api_key = os.environ.get(self.key_env, "")
        self.max_tokens = max_tokens  # None = NO CAP (omit from request body)
        self.temperature = temperature
        self.thinking_budget = thinking_budget  # for reasoning models
        self.disable_reasoning = disable_reasoning  # send reasoning.effort=none

    def complete(self, *, system: str, user: str) -> str:
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
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        # Enable thinking for reasoning models
        if self.thinking_budget > 0:
            body["reasoning"] = {"max_tokens": self.thinking_budget}
        elif self.disable_reasoning:
            body["reasoning"] = {"effort": "none"}

        r = requests.post(
            self.base_url, headers=headers, json=body, timeout=60,
        )
        r.raise_for_status()
        data = r.json()

        # Extract usage for cost tracking
        usage = data.get('usage', {})
        self.last_prompt_tokens = usage.get('prompt_tokens', 0)
        self.last_completion_tokens = usage.get('completion_tokens', 0)
        self.last_cost = data.get('usage', {}).get('total_tokens', 0)  # will be overridden

        content = data['choices'][0]['message']['content']
        return content


class DeepSeekDirectModel(OpenRouterModel):
    """Direct DeepSeek API adapter (api.deepseek.com) — same model ids as
    OpenRouter (deepseek-v4-flash / v4-pro) but no OpenRouter hop. Used when
    DEEPSEEK_API_KEY is set and TORTOISE_EXTRACTOR_PROVIDER != 'openrouter'
    (#1350 — the extractor's LLM calls were hitting OpenRouter connection
    errors under load; the direct API is the same model, different route)."""
    provider = "deepseek-direct"
    base_url = "https://api.deepseek.com/v1/chat/completions"
    key_env = "DEEPSEEK_API_KEY"

    def __init__(self, model_id: str, max_tokens: int | None = None,
                 temperature: float = 0.0, **kw):
        super().__init__(model_id, max_tokens=max_tokens,
                         temperature=temperature, **kw)
        self.api_key = os.environ.get(self.key_env, "")

    def complete(self, *, system: str, user: str) -> str:
        body = {
            "model": self.id,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        r = requests.post(
            self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            json=body, timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)
        return data["choices"][0]["message"]["content"]


# Pre-configured models (eval registry — kept name-stable: tests/model_adapters
# re-exports this; the eval harness and tools reference the names).
MODELS = {
    'deepseek-flash': lambda: OpenRouterModel('deepseek/deepseek-v4-flash', max_tokens=None, temperature=0.0),
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

    if explicit == "deepseek-direct":
        if not ds_key:
            raise ValueError(
                "TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct but "
                "DEEPSEEK_API_KEY is not set — an explicit provider names a "
                "key that isn't configured; never silently routing elsewhere "
                "(#1530 fail-closed)")
        return ("deepseek-direct", "openrouter" if or_key else None)
    if explicit == "openrouter":
        if not or_key:
            raise ValueError(
                "TORTOISE_EXTRACTOR_PROVIDER=openrouter but "
                "OPENROUTER_API_KEY is not set — an explicit provider names a "
                "key that isn't configured; never silently routing elsewhere "
                "(#1530 fail-closed)")
        return ("openrouter", "deepseek-direct" if ds_key else None)
    if explicit:
        raise ValueError(
            f"TORTOISE_EXTRACTOR_PROVIDER={explicit!r} invalid — valid values: "
            f"{' | '.join(_PROVIDER_NAMES)}")
    if ds_key:
        return ("deepseek-direct", "openrouter" if or_key else None)
    if or_key:
        return ("openrouter", None)
    return (None, None)


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

    def complete(self, *, system: str, user: str) -> str:
        if self.fallback is not None and (
                self._failed_over
                or _primary_in_cooldown(self.primary.provider, self.cooldown_s)):
            return self._call(self.fallback, system, user, failover=True)
        try:
            return self._call(self.primary, system, user, failover=False)
        except BaseException as e:  # noqa: BLE001, RUF100 — classify first
            self.errors.append(f"{type(e).__name__}: {e}")
            if is_fatal(e) or self.fallback is None:
                raise
            _note_failure(self.primary.provider, self.cooldown_s)
            self._failed_over = True
            return self._call(self.fallback, system, user, failover=True)

    def _call(self, adapter, system: str, user: str, *, failover: bool) -> str:
        out = adapter.complete(system=system, user=user)
        self.last_route = adapter.provider
        if failover:
            self.route = adapter.provider
            self.failover_used = True
        return out


def _strip_family_prefix(model_id: str) -> str:
    """Direct-route wire normalization (D6): ``deepseek/deepseek-v4-flash`` →
    ``deepseek-v4-flash`` (matches the eval's DeepSeekDirectModel ids)."""
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


def _build_single(provider: str, model_id: str, *, max_tokens, temperature):
    if provider == "deepseek-direct":
        return DeepSeekDirectModel(
            _strip_family_prefix(model_id),
            max_tokens=max_tokens, temperature=temperature)
    return OpenRouterModel(model_id, max_tokens=max_tokens, temperature=temperature)


def build_extractor_model(model_id: str | None = None, *,
                          max_tokens: int | None = 4000,
                          temperature: float = 0.0) -> RoutingModel:
    """Production entry — build the routing extractor model (D7).

    Resolves (primary, fallback) via ``resolve_extractor_provider()`` and
    wraps them in a ``RoutingModel``. ``model_id`` defaults to
    ``TORTOISE_EXTRACT_MODEL`` (or ``deepseek/deepseek-v4-flash``). Builds
    leniently — with NO keys at all it degrades to a single OpenRouter
    adapter (back-compat for direct callers / ``TestModelAdapterBounds``);
    fail-closed is enforced at the pipeline gates, not here. An explicit
    ``TORTOISE_EXTRACTOR_PROVIDER`` whose key is absent raises ValueError
    (config error, everywhere)."""
    if model_id is None:
        model_id = (os.environ.get("TORTOISE_EXTRACT_MODEL", "").strip()
                    or "deepseek/deepseek-v4-flash")
    primary_name, fallback_name = resolve_extractor_provider()
    primary_name = primary_name or "openrouter"  # lenient no-key default (D3)
    primary = _build_single(primary_name, model_id,
                            max_tokens=max_tokens, temperature=temperature)
    fallback = None
    if fallback_name:
        fallback = _build_single(fallback_name, model_id,
                                 max_tokens=max_tokens, temperature=temperature)
    return RoutingModel(primary, fallback,
                        cooldown_s=_failover_cooldown_seconds())
