"""Session-endpoint auth — Supabase JWT verification via JWKS (D1, plan §5.3 #2b).

The two-tier auth model (plan §5.3 #2/#2b): session endpoints (E1–E8:
/session/key, /organizations, /graphs, /invites, member management) authenticate with
a Supabase access token verified server-side via JWKS. The data-plane
(/v1/points, /v1/search, /v1/sessions, /v1/team, /v1/team/keys, MCP) stays on
`tt_` keys via get_current_org.

Verification: fetch `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, verify the
RS256 **or ES256** signature (alg dispatch per token header — #1460: this
project signs ES256; RS256 kept for older projects/selfhost), check issuer +
audience + exp/iat/nbf via PyJWT. JWKS cached with TTL; KID-miss triggers a
refetch (R16). Shared-HMAC (SUPABASE_JWT_SECRET) is rejected — JWKS is the
standard, key-rotation-safe path.

#3284: the fetch is bounded by a HARD TOTAL (``_JWKS_FETCH_TOTAL_S``, applied
outside the ``_fetch_jwks`` seam) rather than httpx's per-phase timeout, and
``prefetch_jwks()`` warms the cache at process start so the first request does
NOT normally pay the fetch. The warm-up makes its bounded attempt with
``arm_cooldown=False``: it never arms the request-path cooldown, so a
boot-time blip cannot arm it; an inherited armed cooldown (it is a module
global, so it survives lifespans) does short-circuit the warm-up itself, at
ZERO fetches. The first request still makes its own bounded attempt — UNLESS
the request-path failure/miss cooldown is already armed, in which case that
request is answered from the cooldown with NO fetch, not with its own attempt.
The bound, not the warm-up, is the #3284 guarantee, and every 503 carries
``Retry-After``.

Issue #1460: the verifier previously only handled RS256 (`jwk["n"]` KeyError
on the EC JWKS → unhandled 500 → no CORS headers → browser CORS-wall →
dashboard login wall). The verify core now delegates to PyJWT
(`pyjwt[crypto] 2.13`, already shipped via `mcp`) with a fail-closed boundary:
every verify-path failure is an HTTPException (401/503), never a raw 500.
See docs/plans/2026-08-18-es256-session-auth.md (9 review cycles + second-model
gate) for the full matrix.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import math
import os
import secrets
import time

import httpx
import jwt as pyjwt
from cryptography.exceptions import UnsupportedAlgorithm
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ybetwichurajbfswfeqa.supabase.co")
_JWKS_URL = f"{_SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
_JWKS_TTL = float(os.environ.get("TORTOISE_JWKS_TTL", "300"))  # seconds
_COOLDOWN_S = float(os.environ.get("TORTOISE_JWKS_COOLDOWN", "30"))  # failure/miss cooldown

# ── the JWKS fetch budget: PER-PHASE narrowing under a HARD TOTAL (#3284) ──
#
# ``httpx.AsyncClient(timeout=<float>)`` is a PER-PHASE timeout (connect /
# read / write / pool), NOT a total deadline — verified against httpx 0.28.1:
# ``Timeout(5.0)`` yields 5.0 for each of the four phases, a 20.0s sum. This is
# the repo's own recorded lesson, twice: ``hosted_api.CONTROL_PLANE_PROBE_PHASES``
# exists for exactly this reason. The pre-#3284 code passed a bare ``5`` here,
# so ONE fetch could burn connect(5) + read(5) ≈ 10s of wall clock, and httpx
# applies ``read`` PER READ OPERATION (a dribbling upstream outlives the sum
# indefinitely). The request path pays up to TWO fetches sequentially — a TTL
# refresh plus a kid-miss refetch (R16, pinned by
# tests/test_session_auth.py::TestConcurrency::test_ttl_refresh_plus_miss_double_fetch)
# — hence the 15–35s first request reported in #3284/#3144.
#
# Two layers, mirroring the control-plane probe's LAYERED TIMEOUT doctrine:
#   * the PHASES narrow first, so an ordinary stall unwinds the httpx client's
#     own machinery (a cancelled socket read, not a worker stranded in one);
#   * ``_JWKS_FETCH_TOTAL_S`` is the HARD deadline, enforced by
#     ``asyncio.timeout`` OUTSIDE the ``_fetch_jwks`` seam — so the bound holds
#     for ANY fetch implementation (a test fake, a future transport swap) and
#     also covers the DNS lookup, which httpx's connect phase cannot cancel
#     (anyio runs getaddrinfo in a thread it will not abandon).
_JWKS_FETCH_PHASES: dict[str, float] = {
    "connect": 1.5,
    "read": 1.5,
    "write": 0.25,
    "pool": 0.25,
}
#: Sum of the phases — deliberately BELOW the hard total so a phase timeout
#: normally fires first and the fetch unwinds by itself.
_JWKS_FETCH_PHASE_TOTAL_S = sum(_JWKS_FETCH_PHASES.values())
#: Margin between the phase sum and the hard total: room for the client to
#: unwind and the cache to arm its failure cooldown once a phase timeout fires.
_JWKS_FETCH_MARGIN_S = 0.5


def _resolve_fetch_total() -> float:
    """The HARD per-fetch deadline (seconds) — env-overridable, clamped.

    ``TORTOISE_JWKS_TIMEOUT`` is documented — and, as of #3284, actually
    IMPLEMENTED — as a TOTAL, not a per-phase timeout. A value below the phase
    sum would let the hard deadline win the race against the phases and strand
    the httpx worker in its socket read (CPython #87185 cannot cancel it), so
    it is clamped UP to the phase sum plus the margin and the clamp is logged.
    NOTE the convention differs from ``monitoring.health_probe_interval`` (the
    shared resolver; ``hosted_api._health_probe_interval`` is only its
    back-compat alias since #2988): that
    function REJECTS a below-floor value and falls back to its default, whereas
    this one clamps up. The direction is deliberate — the floor here IS the
    safe value (a lower hard deadline strands a worker), so the operator's
    requested value is preserved as far as is safe instead of being discarded.

    NON-FINITE values are REJECTED, not clamped (mirrors
    ``monitoring.health_probe_interval``). ``float()`` accepts ``nan`` and ``inf``, and a
    bare ``v > 0`` is NaN-safe but NOT inf-safe: ``inf`` — and ``1e309``, which
    ``float()`` evaluates to ``inf`` — passes it, is not ``< floor``, and lands
    in ``asyncio.timeout(inf)``, which NEVER fires. One env value would silently
    revert the #3284 hard deadline this function resolves. ``nan`` is equally
    meaningless. Both fall back to the floor.
    """
    floor = _JWKS_FETCH_PHASE_TOTAL_S + _JWKS_FETCH_MARGIN_S
    try:
        v = float(os.environ.get("TORTOISE_JWKS_TIMEOUT", floor))
    except ValueError:
        return floor
    if not math.isfinite(v):
        logger.warning(
            "TORTOISE_JWKS_TIMEOUT=%s is not finite — falling back to the "
            "floor %.2fs; an infinite total disables the per-fetch hard "
            "deadline (asyncio.timeout never fires) and a nan one is "
            "meaningless",
            v,
            floor,
        )
        return floor
    if v <= 0:  # zero/negative: the floor is the safe value
        return floor
    if v < floor:
        logger.warning(
            "TORTOISE_JWKS_TIMEOUT=%.2fs is below the JWKS phase total "
            "(%.2fs phases + %.2fs margin) — clamping to %.2fs; a lower total "
            "would strand the fetch worker instead of letting a phase "
            "timeout fire",
            v,
            _JWKS_FETCH_PHASE_TOTAL_S,
            _JWKS_FETCH_MARGIN_S,
            floor,
        )
        return floor
    return v


#: The hard deadline one fetch can never outlive (request path included).
_JWKS_FETCH_TOTAL_S = _resolve_fetch_total()
#: Documented worst case for KEY RESOLUTION on ONE request: a TTL-refresh
#: fetch plus a kid-miss refetch (R16). Both are hard-bounded, so key
#: resolution is bounded by construction rather than by upstream good
#: behaviour. This is NOT an end-to-end request bound: a ``/v1/*`` request
#: adds Fly's proxy, TLS, middleware and the endpoint's own work (e.g. a
#: FalkorDB round trip) on top. The default (4.0s per fetch → 8.0s) sits
#: inside a 10s client connect budget (#3144 records clients with 10–15s
#: budgets and NO retry).
_JWKS_RESOLVE_WORST_CASE_S = 2 * _JWKS_FETCH_TOTAL_S

_MAX_JWKS_BYTES = 65536  # post-buffer JWKS body cap (defense-in-depth; httpx buffers first)
_MAX_TOKEN_BYTES = 16000  # repo-enforced token cap — BELOW the server's ~16KB
# header-line limit (uvicorn/h11 max_incomplete_event_size) so the repo guard —
# not a raw server 400/431 without CORS headers — is the first line of rejection
# (pyjwt 2.13 has no max_length kwarg). Code-review #1467 P2.


class _JWKSCache:
    """In-process {kid: jwk} cache — TTL, stale-serve, kid-aware single-flight,
    failure/miss cooldown. Per-process semantics: the deployment is
    single-worker (no `--workers`), so per-process caching is sound
    (mirrors the note at hosted_api.py:7120).

    Never evicts last-good keys: on ANY failed/empty/malformed fetch the
    previous key set keeps serving (bounded revocation window: TTL + cooldown
    + refetch timeout). Failures arm a cooldown so an outage (or an
    unauthenticated forged-kid flood) cannot cause per-request refetches.
    """

    def __init__(self):
        self._keys: dict[str, dict] | None = None
        self._fetched_at: float = 0.0
        self._last_failure_at: float | None = None  # None = never failed (unarmed)
        self._lock = asyncio.Lock()

    def _retry_after_s(self) -> int:
        """Seconds until the cooldown lets the next fetch attempt through (≥1).

        A 503 here is emitted exactly when verification is unavailable AND the
        cache will refuse to refetch for the rest of its cooldown window, so
        the honest RFC 7231 ``Retry-After`` is what is LEFT of that window —
        not the full period. A client honouring it never wastes a retry on an
        answer that is already determined (#3284: a 503 with no ``Retry-After``
        is indistinguishable from a hard outage). The miss path arms
        ``_last_failure_at`` in the FUTURE (jitter, SEC-001), so the arithmetic
        is intentionally signed-agnostic: it reports the real remaining window.
        ``ceil``, matching the ``#1081`` rate-limit precedent: truncation
        advertises a value that can still be inside the cooling window, inviting
        a retry that is refused.
        """
        if self._last_failure_at is None:
            return max(1, math.ceil(_COOLDOWN_S))
        return max(1, math.ceil(_COOLDOWN_S - (time.monotonic() - self._last_failure_at)))

    async def get(self, force: bool = False, kid: str | None = None,
                  arm_cooldown: bool = True) -> dict[str, dict]:
        """Return {kid: jwk}.

        - TTL-serve when fresh; kid-aware early return when the requested kid
          already resolves (single-flight success path).
        - Cooldown-skipped fetch with no last-good keys → HTTPException 503
          carrying ``Retry-After`` (never returns None — callers must not
          crash on a None key set).
        - Fetch failure / zero-usable-keys / miss (force + kid absent after a
          successful refetch) arm the cooldown (`_last_failure_at`).
        - `force` bypasses the TTL but NOT the cooldown.
        - ``arm_cooldown=False`` is for the BOOT warm-up (#3284): it makes its
          bounded attempt unless an already-armed cooldown short-circuits it
          first, and it does not arm the request-path cooldown, so a boot-time
          blip cannot refuse every request for ``_COOLDOWN_S`` without trying.
          The first real request then makes its own (still bounded) attempt —
          UNLESS that cooldown is already armed (it is a module global, so it
          survives lifespans), in which case the request is answered from the
          cooldown with NO fetch. The #3284 guarantee is untouched.

        WHY the returned set was served (fresh fetch vs a stale last-good
        serve) is available from ``get_with_origin``; ``get`` is the thin
        wrapper for callers that only need the keys.
        """
        keys, _origin = await self._resolve(
            force=force, kid=kid, arm_cooldown=arm_cooldown)
        return keys

    async def get_with_origin(
        self, force: bool = False, kid: str | None = None,
        arm_cooldown: bool = True,
    ) -> tuple[dict[str, dict], str | None]:
        """``get()`` plus WHY the returned key set was served (#3284 boot log).

        ``origin`` is ``None`` when this call has NO stale serve to report —
        the returned set was fetched by this call (it may be EMPTY: the
        ``empty`` outcome for an upstream 200 with zero usable keys), is still
        inside the TTL fast path, or still contains the requested ``kid``.
        Otherwise ``origin`` is a short reason: the fetch raised, the upstream
        answered with zero usable keys while last-good keys were cached, or an
        armed cooldown blocked the attempt — and a previously-cached (STALE)
        set was served.

        The kid-hit early return is TTL-AGNOSTIC: it serves a set that still
        holds the requested ``kid`` WITHOUT fetching or checking freshness, so
        it CAN serve an EXPIRED set and report ``origin is None``. Only the TTL
        fast path and the cooldown-fresh guard in ``_resolve`` check
        ``_JWKS_TTL``. ``origin is None`` therefore means "no stale serve to
        report", never "this call fetched".

        The boot warm-up needs the distinction. With last-good keys cached, a
        failed fetch returns THEM (stale-serve), so keying ``ok`` off the
        returned set alone reports a genuinely down upstream as ``ready``
        (#2922: every failure states its reason). Every other caller wants
        ``get()``.
        """
        return await self._resolve(
            force=force, kid=kid, arm_cooldown=arm_cooldown)

    async def _resolve(
        self, force: bool, kid: str | None, arm_cooldown: bool,
    ) -> tuple[dict[str, dict], str | None]:
        """Shared body of ``get``/``get_with_origin`` — ``(keys, stale_reason)``.

        ``stale_reason is None`` ⇔ there is no stale serve to report: the set
        was fetched by this call (possibly EMPTY — the ``empty`` outcome), was
        served by the TTL fast path, or was served by the TTL-AGNOSTIC kid-hit
        early return — which returns a set that still contains the requested
        ``kid`` without fetching or checking freshness, so it can serve an
        EXPIRED set. Any non-None value means a previously-cached set is being
        served that this call did NOT refresh, and says why.
        """
        now = time.monotonic()
        # The TTL fast path requires a USABLE key set, not just a non-None one:
        # an empty parse leaves ``_fetched_at == 0.0`` with ``_keys == {}``, and
        # on a host whose monotonic clock is below the TTL that reads as
        # "fetched just now", so a bare ``self._keys is not None`` serves ``{}``
        # — presenting an empty set as a fresh cache entry. This is correct
        # ``get()`` semantics and is DEFENSIVE for the current caller:
        # ``verify_session_jwt`` force-refetches on any kid miss (R16), so it
        # recovers from an empty serve — the change closes NO user-visible
        # window today. It is pinned so that ``get()``, as a contract, and any
        # future non-force caller cannot be handed a TTL-"fresh" empty set.
        if not force and self._keys and now - self._fetched_at < _JWKS_TTL:
            return self._keys, None
        if kid is not None and self._keys is not None and kid in self._keys:
            return self._keys, None
        async with self._lock:
            now = time.monotonic()
            if not force and self._keys and now - self._fetched_at < _JWKS_TTL:
                return self._keys, None
            if kid is not None and self._keys is not None and kid in self._keys:
                return self._keys, None
            # Failure/miss cooldown — inside the lock (single-flight: exactly
            # one fetch attempt per cooldown window under concurrency).
            if self._last_failure_at is not None and now - self._last_failure_at < _COOLDOWN_S:
                if self._keys is None:
                    raise HTTPException(
                        status_code=503,
                        detail="Session verification unavailable",
                        headers={"Retry-After": str(self._retry_after_s())},
                    )
                # A TTL-fresh set is not made stale by an armed cooldown.
                # Reachable from any force=True caller (the boot warm-up; also
                # verify_session_jwt's R16 kid-miss refetch):
                # ``force=False`` already took the TTL fast path above, so the
                # cache here is milliseconds old and will serve the first
                # request at zero fetch cost — reporting it ``stale`` was a
                # false alarm introduced by the #3284 stale-serve fix. Guarding
                # on a USABLE set too keeps an empty cache out of the fast path
                # on a host whose monotonic clock is below the TTL.
                if self._keys and now - self._fetched_at < _JWKS_TTL:
                    return self._keys, None
                # Otherwise a cooldown-skipped fetch did NOT refresh: this is a
                # stale serve too, so the warm-up must not report it as "ready".
                return self._keys, (
                    "refetch not attempted — within the "
                    f"{_COOLDOWN_S:.0f}s failure/miss cooldown"
                )
            try:
                content = await _fetch_jwks_bounded()
                if len(content) > _MAX_JWKS_BYTES:
                    raise ValueError("JWKS response exceeds size cap")
                parsed = _parse_jwks(content)
                if not parsed:
                    # Zero usable keys = failure semantics: arm the cooldown,
                    # do NOT refresh the TTL (recovery via force path after the
                    # cooldown lapses, or TTL expiry), keep last-good on warm.
                    if arm_cooldown:
                        self._last_failure_at = time.monotonic()
                    if self._keys is None:
                        self._keys = {}
                    logger.warning("JWKS fetch returned zero usable keys — serving stale/empty")
                    if not self._keys:
                        # Cold (or already-empty) cache: nothing last-good to
                        # serve stale. The warm-up reports this as its own
                        # "empty" outcome, not as a stale serve.
                        return self._keys, None
                    return self._keys, (
                        "upstream JWKS answered 200 with 0 usable keys "
                        "(empty body / bad rotation)"
                    )
                self._keys = parsed
                self._fetched_at = time.monotonic()
                if kid is not None and kid not in parsed and arm_cooldown:
                    # Miss = failure semantics: a forged-kid flood against a
                    # healthy-but-kid-absent upstream must not refetch per
                    # request. Documented tradeoff: a flood can delay a
                    # legitimately rotated key's refetch by ≤ cooldown.
                    # Code-review #1467 SEC-001: JITTER the miss window to
                    # [C, 1.5·C] so a poller cannot deterministically re-arm
                    # the cooldown at expiry (an attacker winning every round
                    # would starve key rotation indefinitely). Failure-arm
                    # below stays deterministic. CSPRNG draw (secrets) —
                    # re-review flagged MT19937 state-recovery as theoretical;
                    # secrets removes the argument at zero cost.
                    # ``and arm_cooldown``: the boot warm-up must not arm the
                    # request-path cooldown (#3284 review P1).
                    self._last_failure_at = time.monotonic() + (
                        secrets.randbelow(int(_COOLDOWN_S * 500)) / 1000
                    )
            except HTTPException:
                raise
            except Exception as exc:  # network, json, shape, size, filter errors
                if arm_cooldown:
                    self._last_failure_at = time.monotonic()
                if self._keys is None:
                    logger.warning("JWKS unavailable (cold) — 503: %s", exc)
                    raise HTTPException(
                        status_code=503,
                        detail="Session verification unavailable",
                        headers={"Retry-After": str(self._retry_after_s())},
                    ) from exc
                logger.warning("JWKS fetch failed — serving stale: %s", exc)
                return self._keys, f"JWKS fetch failed: {type(exc).__name__}: {exc}"[:200]
            return self._keys, None


def _parse_jwks(content: bytes) -> dict[str, dict]:
    """Parse a JWKS body into {kid: jwk}.

    - Entries without a STRING kid (kid-less, or wrong-typed kid VALUE like
      `123`/`true`) are dropped.
    - Duplicate kids: FIRST wins (the previous `{k["kid"]: k ...}` comprehension
      was LAST-wins and could silently switch keys on a bad rotation).
    - Malformed entries raise → the caller treats the whole fetch as failed
      (fail-closed; a partial overwrite of last-good keys is never allowed).
    """
    jwks = json.loads(content)
    keys_list = jwks.get("keys")
    if not isinstance(keys_list, list):
        raise ValueError("JWKS keys is not a list")
    parsed: dict[str, dict] = {}
    for entry in keys_list:
        if not isinstance(entry, dict):
            raise ValueError("JWKS entry is not a dict")
        kid_value = entry.get("kid")
        if not isinstance(kid_value, str) or not kid_value:
            continue  # kid-less / wrong-typed kid dropped
        if kid_value in parsed:
            continue  # first-wins
        parsed[kid_value] = entry
    return parsed


async def _fetch_jwks() -> bytes:
    """Fetch the JWKS body. Seam for tests.

    The transport gets the explicit PER-PHASE timeout, so an ordinary stall
    unwinds the client's own machinery first. The HARD total is applied by
    ``_fetch_jwks_bounded``, OUTSIDE this seam — the bound therefore holds for
    any implementation of this function, including a test fake.
    """
    async with httpx.AsyncClient(timeout=httpx.Timeout(**_JWKS_FETCH_PHASES)) as client:
        resp = await client.get(_JWKS_URL)
        resp.raise_for_status()
        return resp.content


async def _fetch_jwks_bounded() -> bytes:
    """``_fetch_jwks`` under the HARD per-fetch deadline (#3284).

    ``asyncio.timeout`` — not httpx's per-phase knob — is what makes
    ``TORTOISE_JWKS_TIMEOUT`` a *total*. It covers what the phases cannot:
    the DNS lookup (anyio resolves in a thread the timeout cannot abandon, so
    a slow resolver is NOT bounded by httpx's connect phase) and per-read
    dribbling. A timeout surfaces as ``TimeoutError`` and takes the cache's
    ordinary failure path: cooldown armed, stale-serve when last-good keys
    exist, otherwise a bounded ``503`` + ``Retry-After``.
    """
    async with asyncio.timeout(_JWKS_FETCH_TOTAL_S):
        return await _fetch_jwks()


_jwks = _JWKSCache()


async def prefetch_jwks() -> dict:
    """Pre-pay the process's first JWKS fetch at startup (#3284 Move A).

    Before this, the FIRST session-authenticated request on a fresh process
    paid for the fetch inline (``_keys is None``): a cold/slow/retried fetch
    was charged to a user-facing call, which is how a slow 401 becomes a
    client-visible hang. Warm-up is the state of the art here (WorkOS, Okta,
    Auth0 and Clerk all say pre-warm at startup, never lazily fetch on the
    first user request); our cache already had every OTHER property.

    ``force=True`` (the #3284 design round): the warm-up PAYS for a fresh key
    set rather than trusting an inherited, possibly stale in-process cache —
    UNLESS an already-armed cooldown short-circuits it first, at zero fetches
    (the warm-up READS an inherited cooldown even though it never arms one;
    see ``transport_error`` below).

    ``arm_cooldown=False`` is the load-bearing half (#3284 review P1): a warm-up
    runs milliseconds after boot, exactly when DNS/egress are least ready, so a
    single boot-time blip must not spend the ONE attempt the request path is
    allowed and then refuse every request for ``_COOLDOWN_S`` without trying.
    Not arming the cooldown is not the same as ignoring it: the warm-up READS
    an inherited one, so it makes its bounded attempt only if that cooldown is
    not already armed (it is a module global, so it survives lifespans) — an
    armed one short-circuits the warm-up itself at zero fetches. The warm-up
    never arms the request-path cooldown, so the first real request makes its
    own bounded attempt and recovers as soon as the upstream does (the #3284
    bound is untouched) — UNLESS the cooldown is already armed, in which case
    the first real request is answered from the cooldown with NO fetch, not
    with its own attempt.

    Deliberately NON-RAISING — the caller runs it as a background task behind
    the listener, and a warm-up must never break boot. The report carries an
    ``outcome`` so the boot log can be TRUTHFUL about which case it was:

    * ``"ready"`` — a FRESH, usable key set is being served (fetched by this
      call, or already within the TTL), and the first request pays nothing;
    * ``"empty"`` — a 200 with ZERO usable keys (bad rotation / empty body)
      against a COLD cache. This is NOT a transport outage: a zero-key body is
      cached as ``{}`` and verifies as an unknown kid, so the request answers
      **401** "Unknown signing key", never 503, and it re-attempts the fetch
      (the warm-up did not arm the cooldown);
    * ``"stale"`` — the fetch did not yield a fresh usable set (it raised, the
      upstream answered with zero usable keys, or an armed cooldown blocked it)
      but last-good keys WERE cached, so ``get()`` serves the old set. This is
      reported as a failure, with its reason (#2922), instead of as ``ready``
      while the cache logs "serving stale": the first request is served from
      the stale set and a kid miss still triggers its own bounded refetch
      UNLESS that cooldown is still armed (a cooldown an earlier lifespan
      armed also blocks the request path) — which, if it also fails, answers
      **401** from that set (never an unbounded wait and never a 503, since
      last-good keys exist);
    * ``"transport_error"`` — no usable key set could be reported and there
      was NO last-good set to serve stale: either the bounded fetch raised
      (network/timeout/HTTP) with a cold/empty cache, or an already-armed
      failure/miss cooldown blocked the attempt outright (its text,
      ``"refetch not attempted — within the …s failure/miss cooldown"``, is
      carried verbatim in ``error``). The first request makes its own bounded
      attempt UNLESS that cooldown is still armed; it answers a bounded
      ``503`` + ``Retry-After`` when no key set has ever been cached, or
      ``401`` "Unknown signing key" from an empty cached set.

    Returns a small boot-log report:
    ``{"ok", "keys", "elapsed_ms", "error", "outcome"}``.
    """
    start = time.monotonic()

    def _report(ok: bool, keys: int, error: str | None, outcome: str) -> dict:
        return {
            "ok": ok,
            "keys": keys,
            "elapsed_ms": round((time.monotonic() - start) * 1000, 1),
            "error": error,
            "outcome": outcome,
        }

    try:
        keys, stale_reason = await _jwks.get_with_origin(
            force=True, arm_cooldown=False)
    except HTTPException as exc:
        return _report(
            False, 0, f"HTTPException {exc.status_code}: {exc.detail}",
            "transport_error")
    except Exception as exc:  # a warm-up must never raise
        return _report(False, 0, f"{type(exc).__name__}: {exc}"[:200],
                       "transport_error")
    if not keys:
        # Every failure must state its reason (#2922). An empty set usually
        # means a 200 with zero usable keys (bad rotation) — but the cache can
        # also return ``{}`` because a RAISING fetch had no last-good keys to
        # serve, or because an armed cooldown blocked the attempt. Key this off
        # the freshness ORIGIN, not off "did this call fetch?": only
        # ``stale_reason is None`` proves a successfully parsed empty body.
        # Keying off the returned set alone threw the real reason away and told
        # the operator "NOT a transport outage" DURING a transport outage.
        if stale_reason is not None:
            return _report(False, 0, stale_reason, "transport_error")
        return _report(
            False, 0,
            "upstream JWKS answered 200 with 0 usable keys "
            "(empty body / bad rotation)",
            "empty")
    if stale_reason is not None:
        # The fetch did NOT produce a fresh set, but last-good keys existed and
        # were served. Keying off ``keys`` alone reported this as "ready" while
        # ``get()`` logged "serving stale" (#2922 — a fourth case that the
        # empty/transport_error taxonomy collapsed into ready).
        return _report(
            False, len(keys),
            f"{stale_reason} — serving {len(keys)} last-good cached key(s)",
            "stale")
    return _report(True, len(keys), None, "ready")


def _b64url_decode(part: str) -> bytes:
    pad = "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part + pad)


def _decode_header(token: str) -> dict:
    """Parse + shape-check the JWT header only (PyJWT owns payload parsing).

    401 on malformed/oversized input. The 16KB length guard is repo-enforced
    defense-in-depth — pyjwt 2.13 has no `max_length` kwarg, and the effective
    HTTP cap is the server's ~16KB header-line limit anyway.
    """
    if len(token.encode()) > _MAX_TOKEN_BYTES:
        raise HTTPException(status_code=401, detail="Invalid session token")
    parts = token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid session token")
    try:
        header = json.loads(_b64url_decode(parts[0]))
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid session token")  # noqa: B904
    if not isinstance(header, dict):
        # Non-dict header segments (valid JSON: [1,2], 123, "x") must 401,
        # never AttributeError → 500.
        raise HTTPException(status_code=401, detail="Invalid session token")
    return header


async def verify_session_jwt(request: Request) -> dict:
    """Verify the Supabase access token and return {user_id, email, app_metadata}.

    Raises 401 on missing/invalid/expired/malformed token (fail-closed), 503
    when the JWKS is unreachable with no last-good key set. KID-miss triggers a
    single-flight, cooldown-aware JWKS refetch (R16).

    Fail-closed boundary: every non-HTTPException escaping
    PyJWK.from_dict / jwt.decode is converted to 401 — an auth boundary must
    never leak an unhandled exception (a raw 500 lacks CORS headers and is
    misread by browsers as a CORS failure — the #1460 incident class).
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing session token")
    token = auth[7:]

    header = _decode_header(token)
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid.strip():
        # Missing / whitespace / non-string kid: 401 with zero network I/O.
        raise HTTPException(status_code=401, detail="Invalid session token")

    # JWKS fetch boundary — 503 on unreachable/no-last-good, stale-serve if
    # last-good exists (inside get()).
    try:
        keys = await _jwks.get()
        jwk = keys.get(kid)
        if jwk is None:
            keys = await _jwks.get(force=True, kid=kid)  # R16: single-flight, cooldown-aware
            jwk = keys.get(kid)
        if jwk is None:
            raise HTTPException(status_code=401, detail="Unknown signing key")
    except HTTPException:
        raise

    try:
        # Passing the PyJWK object (not jwk.key) keeps PyJWT's alg/kty-confusion
        # defense live: `alg != key.algorithm_name` → InvalidAlgorithmError.
        key = pyjwt.PyJWK.from_dict(jwk)
        claims = pyjwt.decode(
            token,
            key=key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
            issuer=_SUPABASE_URL.rstrip("/") + "/auth/v1",
            leeway=30,  # #750.4 clock-skew grace (old code: exp + 30)
            options={
                "require": ["sub", "exp", "iat", "iss"],  # iss required (old code rejected missing iss)
                "verify_iat": True,  # deltas vs old code: iat/nbf enforced, aud required+strict,
                "verify_nbf": True,  # exact issuer match, exp leeway inclusive
                "strict_aud": True,  # list-form aud rejected (string-exact)
            },
        )
    except pyjwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail="Invalid session token") from e
    except (KeyError, ValueError, TypeError, binascii.Error, OverflowError, RecursionError, UnsupportedAlgorithm) as e:
        # PyJWK.from_dict / wrong-typed / out-of-range claim failures are NOT
        # PyJWTError subclasses — fail closed. (binascii.Error is a ValueError
        # subclass — kept for self-documentation. RecursionError is
        # defensive: json parsers are recursion-limited (runtime-dependent —
        # CPython C-json ~10k nesting, pure-python json ~1k), so a small
        # deeply-nested header can trip it — keep the entry.
        # UnsupportedAlgorithm covers FIPS/ancient-OpenSSL backends. ⛔ All
        # names in this tuple are bound at module/function top — except-clause
        # names are evaluated at match time, before the body runs.)
        raise HTTPException(status_code=401, detail="Invalid session token") from e
    except Exception as e:
        # Fail-closed completeness: the enumerated tuple cannot be proven
        # exhaustive against the "never a raw 500" acceptance. Auth boundary:
        # ANY unhandled exception → 401. (Nothing in this try raises
        # HTTPException — the "Unknown signing key" 401 lives in the outer
        # fetch try, which re-raises HTTPException first.)
        raise HTTPException(status_code=401, detail="Invalid session token") from e

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id.strip():
        # pyjwt's verify_sub rejects non-string subs; the guard covers the
        # empty/whitespace-string case pyjwt accepts.
        raise HTTPException(status_code=401, detail="Invalid session token")
    app_metadata = claims.get("app_metadata")
    if app_metadata is not None and not isinstance(app_metadata, dict):
        # Downstream (claim path) does app_metadata.providers — shape-guard here
        # so a corrupted token cannot 500 one hop later.
        raise HTTPException(status_code=401, detail="Invalid session token")
    email = claims.get("email")
    if email is not None and not isinstance(email, str):
        # Consumers do string ops on user["email"].
        raise HTTPException(status_code=401, detail="Invalid session token")
    return {
        "user_id": user_id,
        "email": email,
        # #1082 (claim path): app_metadata is user-level, always present,
        # survives token refresh — unlike `amr` which is optional and
        # refresh-mutated to `token_refresh`. The claim endpoint asserts
        # app_metadata.providers ∩ {github, google} ≠ ∅ (provider-verified
        # email invariant). Additive key — existing callers read known keys.
        "app_metadata": app_metadata or {},
    }


async def get_current_user(request: Request) -> dict:
    """FastAPI dependency: session-authenticated user (JWT → user_id)."""
    return await verify_session_jwt(request)
