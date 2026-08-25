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
    flash family's NON-reasoning id (``deepseek-chat``) on the wire (pilot
    #1549 — ``deepseek-v4-flash`` reasons by default and collapses to empty
    output), the openrouter route sends the spec unchanged (D6).

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
        try:
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
        if os.environ.get("TORTOISE_JSON_MODE", "1") == "1":
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
    The flash family uses the NON-reasoning id ``deepseek-chat`` on the wire
    (pilot #1549: api.deepseek.com's ``deepseek-v4-flash`` reasons by default
    and collapses to empty output — 1500/1500 reasoning tokens, finish=length);
    ``deepseek-v4-pro`` stays as-is (no collapse evidence — pending
    verification). Used when DEEPSEEK_API_KEY is set and
    TORTOISE_EXTRACTOR_PROVIDER != 'openrouter' (#1350 — the extractor's LLM
    calls were hitting OpenRouter connection errors under load; the direct
    API is the same model, different route)."""
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
    # Pilot #1549 (50-Q run, 2026-08-25): the direct API id must be the
    # NON-reasoning variant. api.deepseek.com's ``deepseek-v4-flash`` reasons
    # by default and burns the whole max_tokens budget on hidden reasoning
    # tokens for non-trivial prompts (observed: 1500/1500 reasoning tokens,
    # finish_reason=length, ZERO output content) — S1 then returns an empty
    # story, S2/S4 never run, and extraction silently produces no points.
    # ``deepseek-chat`` is the direct API's non-reasoning chat model
    # (empirically verified: full story output, finish_reason=stop).
    'deepseek-flash-direct': lambda: DeepSeekDirectModel('deepseek-chat', max_tokens=None, temperature=0.0),
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
        if failover:
            self.route = adapter.provider
            self.failover_used = True
        return out


def _strip_family_prefix(model_id: str) -> str:
    """Direct-route wire normalization (D6): ``deepseek/deepseek-chat`` →
    ``deepseek-chat``. Intermediate step feeding ``_direct_wire_id`` (which
    then remaps the flash family onto its non-reasoning direct id)."""
    return model_id.rsplit("/", 1)[-1] if "/" in model_id else model_id


def _direct_wire_id(model_id: str) -> str:
    """Direct-API wire id (D6 + pilot #1549): api.deepseek.com's
    ``deepseek-v4-flash`` reasons by DEFAULT and burns the whole max_tokens
    budget on hidden reasoning for non-trivial prompts (observed 1500/1500
    reasoning tokens, finish_reason=length, ZERO content) — S1 collapses to
    an empty story and extraction silently produces no points. The direct
    route therefore sends the non-reasoning ``deepseek-chat`` for the flash
    family; ``deepseek-v4-pro`` is unchanged (no collapse evidence — out of
    scope pending verification)."""
    bare = _strip_family_prefix(model_id)
    return "deepseek-chat" if bare in ("deepseek-v4-flash", "deepseek-chat") else bare


def _build_single(provider: str, model_id: str, *, max_tokens, temperature):
    if provider == "deepseek-direct":
        return DeepSeekDirectModel(
            _direct_wire_id(model_id),
            max_tokens=max_tokens, temperature=temperature)
    if provider == "venice":
        # Venice's catalog serves the flash id (docstring); the non-reasoning
        # 'deepseek-chat' id is unverified there — keep the documented id. A
        # wrong id fails LOUD via preflight (config-4xx → fatal gate), never
        # silently; verify the venice catalog before enabling the pool (#1549).
        bare = _strip_family_prefix(model_id)
        if bare == "deepseek-chat":
            bare = "deepseek-v4-flash"
        return VeniceModel(
            bare,
            max_tokens=max_tokens, temperature=temperature)
    return OpenRouterModel(model_id, max_tokens=max_tokens, temperature=temperature)


class RotatingModel:
    """3-provider pool with round-robin rotation + per-provider cooldown
    (pilot #1549 — the DeepSeek direct API degrades to 15-90s/call under
    sustained load; three independent GPU farms with uncorrelated
    reliability spread the load AND give redundancy).

    ``complete()`` routes each call to the next healthy provider in the
    rotation; a transient failure puts that provider in cooldown (skipped
    for ``cooldown_s``) and the next provider is tried; fatal errors
    (401/402/403 + config 4xx, P2 taxonomy) re-raise immediately — no
    rotation on auth/billing failures. Exposes the capture-meta contract:
    ``provider``/``route`` (active provider), ``errors``, ``last_finish_reason``
    (the truncation signal — read from the serving adapter), ``close()``
    (interrupt a hung read — the #1655 fix, applied to the active adapter)."""
    def __init__(self, providers: list, *, cooldown_s: float = 300.0,
                 weights: list[float] | None = None):
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
                return out
            except Exception as e:  # noqa: BLE001
                last_err = e
                if is_fatal(e):
                    raise  # never rotate on auth/billing/config failures
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
                try:
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
    # Pilot #1549 (2026-08-25): the DIRECT-API flash id must be the
    # non-reasoning variant. api.deepseek.com's ``deepseek-v4-flash`` reasons
    # by default and burns the max_tokens budget on hidden reasoning for
    # non-trivial prompts (1500/1500 reasoning tokens observed,
    # finish_reason=length, ZERO content) — S1 collapses to an empty story
    # and extraction silently produces no points. The key maps to the
    # FAMILY-PREFIXED ``deepseek/deepseek-chat`` so every route gets a valid
    # wire id: the direct lane strips to the non-reasoning ``deepseek-chat``
    # (``_direct_wire_id``), the OpenRouter lane keeps the valid prefixed id,
    # and the venice lane serves its documented catalog id. ``deepseek-v4-pro-direct``
    # is unchanged (no collapse evidence for v4-pro — pending verification).
    # The v4-pro keys are ALSO family-prefixed so the OpenRouter pool lane
    # gets a valid id (bare ids 404 there → fatal → pool-kill, #1549 class).
    "deepseek-flash-direct": "deepseek/deepseek-chat",
    "deepseek-v4-pro-direct": "deepseek/deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "deepseek-v4-pro-noreason": "deepseek/deepseek-v4-pro",
    "deepseek-r1-xhigh": "deepseek/deepseek-r1-0528",
    "deepseek-v4-pro-xhigh": "deepseek/deepseek-v4-pro",
}


def build_extractor_model(model_id: str | None = None, *,
                          max_tokens: int | None = 4000,
                          temperature: float = 0.0) -> RoutingModel:
    """Production entry — build the routing extractor model (D7).

    Resolves (primary, fallback) via ``resolve_extractor_provider()`` and
    wraps them in a ``RoutingModel``. ``model_id`` defaults to
    ``TORTOISE_EXTRACT_MODEL`` (or ``deepseek/deepseek-v4-flash``; the
    default's direct route sends the non-reasoning ``deepseek-chat`` on the
    wire — pilot #1549). Builds
    leniently — with NO keys at all it degrades to a single OpenRouter
    adapter (back-compat for direct callers / ``TestModelAdapterBounds``);
    fail-closed is enforced at the pipeline gates, not here. An explicit
    ``TORTOISE_EXTRACTOR_PROVIDER`` whose key is absent raises ValueError
    (config error, everywhere)."""
    if model_id is None:
        model_id = (os.environ.get("TORTOISE_EXTRACT_MODEL", "").strip()
                    or "deepseek/deepseek-v4-flash")
    # Registry-key normalization (pilot #1549 fix) — unknown strings pass
    # through untouched (raw specs stay valid).
    model_id = _REGISTRY_KEY_TO_ID.get(model_id, model_id)
    primary_name, pool_names = resolve_extractor_provider()
    if not pool_names:
        pool_names = ["openrouter"]  # lenient no-key default (D3)
    providers = [_build_single(p, model_id, max_tokens=max_tokens,
                               temperature=temperature)
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
                             weights=w)
    return RoutingModel(providers[0], providers[1] if len(providers) > 1 else None,
                        cooldown_s=_failover_cooldown_seconds())
