"""Outbound webhook delivery for the session-event stream — #2081 (epic #2080 W4 phase 2).

The delivery half of Topology C in
``docs/research/2026-09-01-gbrain-learnings/delivery-shape.md``: apps subscribe
to the durable ``:GraphEvent`` stream (written by ``event_store`` on every
graph mutation), and this module pushes those events to the subscriber's URL.

WHY IT EXISTS. The subscriber path that ships today is PULL — ``GET /v1/events``
+ ``tortoise_events_poll`` (``read_after`` on a cursor). A third-party app that
cannot hold a poll loop (a serverless app, a harness hook) has no way to learn
that its memory changed. This is the push half: a subscription registry, a
dispatcher, retry/backoff, and delivery receipts.

THE CONTRACTS THIS MODULE PINS

1. **At-least-once, ordered, per subscription.** A subscription's ``cursor``
   advances ONLY after a 2xx. A crash between the POST and the cursor write
   re-delivers the same event, so the consumer MUST be idempotent — the
   ``X-Tortoise-Delivery`` header (``<sub_id>:<seq>``) is stable across
   re-deliveries and ``event_id`` is the durable event identity.
2. **No EP work on the push path.** ``dispatch_pending`` READS
   ``:GraphEvent`` rows; the payload is the durable event payload computed at
   write time. Nothing here calls ``compute_confidence`` or opens the EP
   engine, so a push can never re-run belief propagation (indicator 2 of
   #2081: a precomputed payload served from the store, never a per-push
   recompute). The *why-block assembly* half of the phase — enriching these
   events with support chains/NANDs — is deliberately NOT here; it belongs to
   the dream-cycle precompute follow-up so this module has no dependency on
   the EP engine.
3. **Best-effort, never a caller's failure.** ``dispatch_pending`` never
   raises: a subscriber's dead endpoint, a TLS failure, or a graph hiccup
   becomes a receipt and a backoff, never an exception on the capture path.
   Delivery never runs inside a mutation: it runs at the END of a tenant's
   dream cycle (``hosted_api`` ``_dream_worker``), from the SCHEDULED RETRY
   SWEEP (#8098 — ``hosted_api._webhook_retry_sweep_pass``), and on demand.
3b. **Two triggers, one due predicate (#8098).** The dream cycle is the
   cadence for NEW events (a write enqueues the dream that pushes it), but it
   is a WRITE-triggered cadence: for an idle tenant no dream runs, so a retry
   whose ``next_attempt_at`` elapses would wait for that tenant's next write.
   The sweep is the second trigger, and both it and the dispatcher build their
   query from the SAME two module-level clauses below — ``_DUE_PREDICATE``
   (is it due by TIME) and ``_BEHIND_PREDICATE`` (is it actually BEHIND the
   stream) — so a change to one can never leave the other behind. The gate
   admits a graph only when the answer to both is yes, which is the condition
   that makes "one SDK open only for the graphs that answered yes" true: see
   the ``#8098 review F3`` notes on those constants for what the temporal half
   alone was admitting (every tenant that had ever registered a webhook,
   forever).
4. **Retry is bounded, then the subscription is DISABLED.** Exponential
   backoff (``BASE_BACKOFF_S * 2**(attempt-1)``, capped at
   ``MAX_BACKOFF_S``); after ``max_attempts`` consecutive failures the
   subscription stops being dispatched (``disabled_at`` set with a reason) so
   one dead endpoint cannot consume dispatch capacity forever. Re-enabled by
   deleting and re-creating the subscription.
5. **Signing.** ``X-Tortoise-Signature: t=<unix>,v1=<hex>`` — HMAC-SHA256 over
   ``b"<t>." + body`` with the subscription secret (the Stripe scheme).
   ``verify_signature`` is the consumer-side half and is exported so the pilot
   app and its tests share one implementation. The secret is returned ONCE, at
   creation, and never by a read path; it is stored encrypted via
   ``crypto.encrypt_token`` (Fernet, ``TORTOISE_ENCRYPTION_KEY``), the same
   at-rest treatment as the OAuth tokens on the Org node.
6. **The destination is judged at CONNECT time, not only at registration.**
   ``create_subscription`` refuses a non-global IP literal and a local name,
   but it does not resolve DNS — so the answer DNS gives at DISPATCH is the
   first real look at the destination. ``_pinned_targets`` resolves, refuses
   any non-global answer (and any MIXED answer, whole), and dials the vetted
   ADDRESS with ``Host``/SNI still on the hostname. A refusal is a receipt
   (``retrying``/``exhausted``), never a silent success — see the transport
   section (#8103, closing the residual #2081 documented).

STORAGE (graph-native, per-graph = per-org — mirrors ``event_store``: the
namespace IS the partition, there is no ``org_id`` property):

* ``:ContextSubscription {sub_id, url, secret, types, description, created_at,
  created_by, cursor, attempts, next_attempt_at, disabled_at, disabled_reason,
  delivered, failed, last_error, last_delivery_at}``
* ``:DeliveryReceipt {receipt_id, sub_id, event_id, seq, attempt, status,
  http_status, error, ts, duration_ms}`` — one per ATTEMPT, capped per
  subscription.
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import os
import ssl
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from tortoise.env_truthy import FALSY, env_flag

from .ids import ulid
from .security import (
    env_int,
    redact_error,
    resolve_webhook_addresses,
    validate_webhook_url,
)

logger = logging.getLogger("tortoise.webhook_delivery")

#: Kill switch (mirrors gbrain's ``GBRAIN_HOOKS=0``, the transferable contract
#: named in the delivery-shape research). Falsy value → ``dispatch_pending``
#: is a no-op; registration still works so a tenant can stage a subscription
#: before ops flips egress on.
KILL_SWITCH_ENV = "TORTOISE_WEBHOOK_DISPATCH"

#: #8098: the scheduled retry sweep. Interval BETWEEN passes, in seconds. The
#: retry-resume target is "within ≤ one interval of ``next_attempt_at``", so
#: this is the knob that bounds retry latency for an IDLE tenant; the first
#: retry backoff is ``BASE_BACKOFF_S`` (30s), hence the 60s default (≤30s of
#: overshoot on the shortest backoff).
SWEEP_INTERVAL_ENV = "TORTOISE_WEBHOOK_SWEEP_INTERVAL"
SWEEP_INTERVAL_DEFAULT_S = 60
#: Enable knob for the SWEEP alone (tristate, default ON). Separate from the
#: ``TORTOISE_WEBHOOK_DISPATCH`` kill switch so an operator can stop the
#: always-on fleet sweep without disabling the write-triggered dream-cycle push
#: (and vice versa: the kill switch stops BOTH — it is honoured by the sweep too).
SWEEP_ENABLED_ENV = "TORTOISE_WEBHOOK_SWEEP"
#: Graph-dispatch concurrency within ONE pass. Each graph is an independent
#: connect + read (+ HTTP when due), so a pass over N idle tenants is N serial
#: round trips at 1; bounded because every graph is a real connection and the
#: pass must not become a fleet-wide connection stampede.
SWEEP_CONCURRENCY_ENV = "TORTOISE_WEBHOOK_SWEEP_CONCURRENCY"
SWEEP_CONCURRENCY_DEFAULT = 4
SWEEP_CONCURRENCY_MAX = 16

#: Consecutive failures before a subscription is disabled (no further dispatch).
MAX_ATTEMPTS_DEFAULT = 8
#: First retry delay, doubled per consecutive failure.
BASE_BACKOFF_S = 30
#: Retry-delay ceiling (1h) — a subscriber down for a day is retried hourly.
MAX_BACKOFF_S = 3600
#: Per-request connect/read/write timeout for a delivery POST.
TIMEOUT_S = 10
#: Events a single subscription may move per dispatch pass.
DEFAULT_BATCH = 50
#: Subscriptions a single graph may hold (abuse bound on the fan-out surface).
MAX_SUBSCRIPTIONS = 25
#: Receipts retained per subscription (newest kept; older attempts pruned).
MAX_RECEIPTS = 500
#: Tolerated clock skew when a consumer verifies a signature.
SIGNATURE_TOLERANCE_S = 300

_SCHEMA_ATTR = "_tortoise_webhook_schema"

#: ``signature`` is deliberately NOT in this list — it is only ever read to
#: sign a delivery and must never reach a response.
_PUBLIC_SUB_FIELDS = (
    "sub_id", "url", "types", "description", "created_at", "created_by",
    "cursor", "attempts", "next_attempt_at", "disabled_at", "disabled_reason",
    "delivered", "failed", "last_error", "last_delivery_at",
)


def dispatch_enabled() -> bool:
    """Is outbound webhook egress enabled? (kill switch; default ON)."""
    raw = os.environ.get(KILL_SWITCH_ENV)
    if raw is None:
        return True
    value = raw.strip().lower()
    # Share the canonical vocabulary instead of declaring a literal (#4097).
    # Semantics are unchanged and deliberately NOT `is_truthy`: this is a kill
    # switch, so an UNKNOWN value keeps egress ON rather than off.
    return value != "" and value not in FALSY


def backoff_seconds(attempt: int) -> int:
    """Delay before retry number ``attempt`` (1-based): 30s, 60s, … capped 1h."""
    attempt = max(1, int(attempt))
    # Cap the EXPONENT, not just the result: 2**(attempt-1) with a corrupt
    # stored attempt count would otherwise build a number big enough to
    # overflow/materalize before the min() could clamp it (the
    # ``capture_spool`` clamp, same reason).
    exponent = min(attempt - 1, 16)
    return min(BASE_BACKOFF_S * (2 ** exponent), MAX_BACKOFF_S)


def max_attempts() -> int:
    return env_int("TORTOISE_WEBHOOK_MAX_ATTEMPTS", MAX_ATTEMPTS_DEFAULT)


def sweep_enabled() -> bool:
    """Is the SCHEDULED retry sweep armed? (tristate, default ON).

    ``env_flag`` semantics, deliberately: unset/blank/garbage resolve to the
    default, so a typo can never silently disarm the one trigger that retries an
    idle tenant. A deployment that wants it off says so explicitly (``0``).
    """
    return env_flag(SWEEP_ENABLED_ENV, True)


def sweep_interval_s() -> int:
    """Validate ``TORTOISE_WEBHOOK_SWEEP_INTERVAL`` to a positive int.

    Same contract as ``monitoring.event_retention_interval``: anything that is
    not a positive whole number of seconds falls back to the default with a
    warning. ``0``/negative is REFUSED rather than accepted because
    ``asyncio.sleep(0)`` returns immediately — an accepted zero would turn the
    sweep into a busy loop against every tenant graph in the fleet.
    """
    raw = os.environ.get(SWEEP_INTERVAL_ENV)
    if raw is None or not str(raw).strip():
        return SWEEP_INTERVAL_DEFAULT_S
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(
            "%s=%r is not a whole number of seconds — using %ds",
            SWEEP_INTERVAL_ENV, raw, SWEEP_INTERVAL_DEFAULT_S)
        return SWEEP_INTERVAL_DEFAULT_S
    if value < 1:
        logger.warning(
            "%s=%r is not a positive number of seconds — using %ds (a "
            "zero/negative interval would sweep the fleet in a busy loop)",
            SWEEP_INTERVAL_ENV, raw, SWEEP_INTERVAL_DEFAULT_S)
        return SWEEP_INTERVAL_DEFAULT_S
    return value


def sweep_concurrency() -> int:
    """Clamp ``TORTOISE_WEBHOOK_SWEEP_CONCURRENCY`` to ``[1, MAX]``."""
    value = env_int(SWEEP_CONCURRENCY_ENV, SWEEP_CONCURRENCY_DEFAULT)
    return max(1, min(int(value), SWEEP_CONCURRENCY_MAX))


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _now() -> datetime:
    return datetime.now(UTC)


# ── Schema ────────────────────────────────────────────────────────────────

def ensure_subscription_schema(proj) -> None:
    """Create the exact-match indexes (idempotent, non-fatal; ``event_store`` pattern)."""
    if getattr(proj, _SCHEMA_ATTR, False):
        return
    for stmt in (
        "CREATE INDEX FOR (s:ContextSubscription) ON (s.sub_id)",
        "CREATE INDEX FOR (r:DeliveryReceipt) ON (r.sub_id)",
        "CREATE INDEX FOR (r:DeliveryReceipt) ON (r.receipt_id)",
    ):
        try:
            proj.g.query(stmt)
        except Exception:  # noqa: BLE001, RUF100
            logger.debug("webhook_delivery: index may already exist: %s", stmt)
    setattr(proj, _SCHEMA_ATTR, True)


# ── Registry ──────────────────────────────────────────────────────────────

def _public(props: dict) -> dict:
    out = {k: props.get(k) for k in _PUBLIC_SUB_FIELDS}
    out["secret_set"] = bool(props.get("secret"))
    out["active"] = not props.get("disabled_at")
    out["types"] = list(props.get("types") or [])
    out["cursor"] = int(props.get("cursor") or 0)
    out["attempts"] = int(props.get("attempts") or 0)
    return out


def _validated_types(types) -> list[str]:
    """Validate an event-type filter against the registry (``events_poll`` contract)."""
    if not types:
        return []
    if isinstance(types, str):
        types = [t.strip() for t in types.split(",") if t.strip()]
    from .shared_state.events import event_types

    registered = event_types()
    unknown = [t for t in types if t not in registered]
    if unknown:
        raise ValueError(f"unknown event type: {unknown[0]}")
    return list(dict.fromkeys(types))


def create_subscription(proj, *, url: str, types=None, description: str = "",
                        created_by: str | None = None,
                        now: datetime | None = None) -> dict:
    """Register a webhook target. Returns the record INCLUDING the one-time ``secret``.

    Raises ``ValueError`` for a rejected url / unknown event type, and
    ``RuntimeError`` when ``TORTOISE_ENCRYPTION_KEY`` is unset (a webhook
    secret is a long-lived credential; it is never stored in the clear —
    ``crypto.encrypt_token`` fails loudly rather than silently downgrading).
    """
    url = validate_webhook_url(url)
    type_list = _validated_types(types)
    ensure_subscription_schema(proj)
    if count_subscriptions(proj, include_disabled=True) >= MAX_SUBSCRIPTIONS:
        raise ValueError(
            f"this graph already holds {MAX_SUBSCRIPTIONS} webhook "
            f"subscriptions — delete one before adding another")
    from .crypto import encrypt_token

    secret = "whsec_" + ulid().replace("-", "")[:32]
    sub_id = "sub_" + ulid()
    now_dt = now or _now()
    proj.g.query(
        "CREATE (s:ContextSubscription {"
        "sub_id:$sub_id, url:$url, secret:$secret, types:$types, "
        "description:$description, created_at:$created_at, "
        "created_by:$created_by, cursor:0, attempts:0, delivered:0, failed:0})",
        params={
            "sub_id": sub_id, "url": url,
            "secret": encrypt_token(secret), "types": type_list,
            "description": str(description or "")[:500],
            "created_at": _iso(now_dt), "created_by": created_by,
        },
    )
    record = get_subscription(proj, sub_id) or {}
    record["secret"] = secret  # one-time; read paths never carry it
    record["signature_header"] = "X-Tortoise-Signature"
    return record


def get_subscription(proj, sub_id: str) -> dict | None:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) RETURN properties(s)",
        params={"id": sub_id},
    ).result_set
    if not rows:
        return None
    return _public(rows[0][0])


def list_subscriptions(proj) -> list[dict]:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription) RETURN properties(s) "
        "ORDER BY s.created_at ASC"
    ).result_set
    return [_public(r[0]) for r in rows]


def count_subscriptions(proj, *, include_disabled: bool = False) -> int:
    where = "" if include_disabled else "WHERE s.disabled_at IS NULL "
    rows = proj.g.query(
        f"MATCH (s:ContextSubscription) {where}RETURN count(s)"
    ).result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def delete_subscription(proj, sub_id: str) -> bool:
    """Delete a subscription and its receipts. Returns False if it never existed."""
    rows = proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) RETURN count(s)",
        params={"id": sub_id},
    ).result_set
    if not rows or not rows[0][0]:
        return False
    # Two statements, not one OPTIONAL MATCH: an OPTIONAL MATCH fans the row out
    # per receipt, so a `count(s)` RETURN would report the RECEIPT count and a
    # missing subscription could read as a hit once receipts existed.
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) DETACH DELETE s",
        params={"id": sub_id},
    )
    proj.g.query(
        "MATCH (r:DeliveryReceipt {sub_id:$id}) DETACH DELETE r",
        params={"id": sub_id},
    )
    return True


def read_receipts(proj, sub_id: str, limit: int = 50) -> list[dict]:
    """Delivery attempts for a subscription, NEWEST first."""
    limit = max(1, min(int(limit), 200))
    rows = proj.g.query(
        "MATCH (r:DeliveryReceipt {sub_id:$id}) RETURN properties(r) "
        "ORDER BY r.ts DESC, r.receipt_id DESC LIMIT $limit",
        params={"id": sub_id, "limit": limit},
    ).result_set
    return [r[0] for r in rows]


def _write_receipt(proj, *, sub_id: str, event: dict, attempt: int, status: str,
                   http_status: int | None, error: str | None,
                   now: datetime, duration_ms: int) -> None:
    ensure_subscription_schema(proj)
    proj.g.query(
        "CREATE (r:DeliveryReceipt {receipt_id:$rid, sub_id:$sid, "
        "event_id:$eid, seq:$seq, attempt:$attempt, status:$status, "
        "http_status:$http, error:$error, ts:$ts, duration_ms:$ms})",
        params={
            "rid": "rcp_" + ulid(), "sid": sub_id,
            "eid": event.get("event_id"), "seq": int(event.get("seq") or 0),
            "attempt": int(attempt), "status": status, "http": http_status,
            "error": error, "ts": _iso(now), "ms": int(duration_ms),
        },
    )
    # Prune: keep the newest MAX_RECEIPTS for this subscription.
    try:
        proj.g.query(
            "MATCH (r:DeliveryReceipt {sub_id:$sid}) "
            "WITH r ORDER BY r.ts DESC, r.receipt_id DESC SKIP $keep "
            "DETACH DELETE r",
            params={"sid": sub_id, "keep": MAX_RECEIPTS},
        )
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("webhook_delivery: receipt prune failed for %s", sub_id)


# ── Signing ───────────────────────────────────────────────────────────────

def sign(secret: str, ts: int, body: bytes) -> str:
    """HMAC-SHA256 over ``b"<ts>." + body`` — the value of the ``v1=`` field."""
    mac = hmac.new(secret.encode("utf-8"),
                   str(int(ts)).encode("ascii") + b"." + body, sha256)
    return mac.hexdigest()


def signature_header(secret: str, ts: int, body: bytes) -> str:
    return f"t={int(ts)},v1={sign(secret, ts, body)}"


def verify_signature(secret: str, header: str, body: bytes, *,
                     tolerance_s: int = SIGNATURE_TOLERANCE_S,
                     now: int | None = None) -> bool:
    """Consumer-side verification (exported so a pilot app and its tests share it).

    Returns False — never raises — for a malformed header, an out-of-tolerance
    timestamp, or a mismatched signature. Comparison is ``hmac.compare_digest``.
    """
    try:
        fields = dict(p.split("=", 1) for p in str(header or "").split(","))
        ts = int(fields["t"])
        expected = sign(secret, ts, body)
    except Exception:  # noqa: BLE001, RUF100
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - ts) > tolerance_s:
        return False
    return hmac.compare_digest(expected, str(fields.get("v1", "")))


# ── Transport ─────────────────────────────────────────────────────────────

#: Default port per scheme — used to decide whether the ``Host`` header may
#: omit the port when the request is re-pointed at a vetted address (#8103).
_DEFAULT_PORTS = {"http": 80, "https": 443}

#: How many vetted addresses one delivery may DIAL. Every answer is validated
#: (a mixed answer is refused whole), and the loop is bounded by COUNT here while
#: the shared budget in ``WebhookTransport.post`` bounds it by TIME — both are
#: needed: each candidate is a separate socket and a separate TLS handshake, so
#: time alone would still open as many sockets as the resolver invented inside
#: the budget. Same number as ``cimd.MAX_ADDRESSES``.
MAX_PINNED_ADDRESSES = 8


def _pinned_targets(url: str, *, resolver=None
                    ) -> tuple[tuple[str, str, str | None], ...]:
    """Return the CONNECT-TIME pin candidates for ``url``, one per vetted address.

    Each entry is ``(pinned_url, host_header, sni_hostname)``.

    The residual #2081 left documented: the registration predicate
    (``security.validate_webhook_url``) never resolves, so the name is only
    judged at DISPATCH — by the resolver, whose answer nothing checked. A name
    that is public at registration and re-pointed afterwards (DNS rebinding),
    or one that was private all along, would make this process sign and POST an
    event payload to an address the tenant never should have reached.

    So the delivery resolves FIRST, judges EVERY answer with the shared policy
    :func:`security.is_global_address` — the same predicate the registration gate
    applies to a literal, wrapped for the connect half in
    :func:`security.resolve_webhook_addresses` — and then pins the socket: the returned URL carries the VETTED
    ADDRESS, while the returned ``host_header`` and ``sni`` keep the HTTP
    ``Host`` header and the TLS server name on the hostname. Resolve-then-
    hand-the-name-to-the-client would only NARROW the TOCTOU window (the
    client resolves again); dialing the address we vetted REMOVES it.

    ``validate_webhook_url`` runs first as a defence-in-depth re-check: the URL
    being dispatched is a value read back from the graph, not a value that
    passed through registration in this process, so the full registration
    policy is re-applied rather than assumed. Both halves therefore share ONE
    policy home (``security``) instead of growing a second list here.

    ONE CANDIDATE PER VETTED ADDRESS, in resolver order — the caller fails over
    between them. Pinning to a single address would have quietly dropped the
    multi-address failover the un-pinned transport inherited from
    ``socket.create_connection`` (a dual-stack target whose first answer is
    unreachable on this host would stop delivering); the addresses are all
    vetted, so trying them in turn keeps that behaviour AND the pin.
    """
    validate_webhook_url(url)
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").strip().lower().rstrip(".")
        port = parts.port
    except ValueError as e:  # malformed IPv6 literal / bad port
        raise ValueError(f"webhook url is not parseable: {e}") from e
    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        raise ValueError(
            f"webhook url scheme must be http or https, not {scheme or '(none)'}")
    if not host:
        raise ValueError("webhook url has no host")
    default_port = _DEFAULT_PORTS[scheme]
    port = port or default_port
    # The name must be ASCII before it is used ANYWHERE: ``Host`` is a header
    # value and ``sni_hostname`` a TLS server name, and httpx encodes neither
    # with a utf-8 fallback — so handing it the raw IDN form raises
    # ``UnicodeEncodeError`` on EVERY attempt. That is not a ``ConnectError``,
    # so it lands in ``post``'s catch-all, the delivery is recorded as failed,
    # and ``MAX_ATTEMPTS_DEFAULT`` then DISABLES the subscription: an IDN
    # callback host that delivers today would go silently to zero deliveries.
    # Pre-pin delivery worked because httpx's own urlparse IDNA-encoded the name
    # before dialing — so normalise ONCE, here (``bücher.example.com`` →
    # ``xn--bcher-kva.example.com``), and use the punycode form for the resolver,
    # the ``Host`` header and SNI alike. It is also the form libc resolves and
    # the form the subscriber's vhost and certificate are keyed on. IPv6
    # literals are already ASCII and pass through unchanged.
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except UnicodeError as e:
        raise ValueError(
            f"webhook url host {host!r} is not a valid IDNA name ({e}) — "
            f"refusing to deliver") from e
    # Every answer must be global; a mixed answer is refused WHOLE. Only then
    # are addresses chosen, and they are what gets dialed.
    addresses = resolve_webhook_addresses(ascii_host, port, resolver=resolver)
    target = parts.path or "/"
    if parts.query:
        target = f"{target}?{parts.query}"
    bracketed = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    host_header = bracketed if port == default_port else f"{bracketed}:{port}"
    # SNI (and therefore certificate verification) stays on the NAME: httpcore
    # passes ``request.extensions['sni_hostname']`` to ``start_tls`` and httpx
    # forwards extensions to httpcore.
    sni = ascii_host if scheme == "https" else None
    candidates: list[tuple[str, str, str | None]] = []
    for address in addresses[:MAX_PINNED_ADDRESSES]:
        literal = f"[{address}]" if ":" in address else address
        candidates.append((f"{scheme}://{literal}:{port}{target}", host_header, sni))
    return tuple(candidates)


class WebhookTransport:
    """The one place an outbound POST happens. Injectable for tests.

    ``follow_redirects`` is ``False`` on purpose: the registration-time SSRF
    predicate (``security.validate_webhook_url``) blessed THIS url, and a
    redirect would let the subscriber move the actual request somewhere the
    predicate never saw. A 3xx is therefore a FAILED delivery (the caller
    sees the status in the receipt).

    The CONNECT-TIME half of that control is :func:`_pinned_targets` (#8103):
    the target is resolved and judged here, and the request goes to a vetted
    address — so a rebound name cannot reach what registration refused, while a
    dual-stack target keeps its multi-address failover. A refusal is returned as
    ``(None, reason)``, i.e. an ordinary failed delivery that
    ``dispatch_pending`` records as a ``retrying``/``exhausted`` receipt: never
    a raise, and never a silent success.

    ``trust_env=False`` is load-bearing, not hygiene: an env-configured proxy
    would take an absolute-form request and resolve the NAME itself, which
    silently discards the pin and lets DNS decide the destination again. A
    direct connection is what makes the check meaningful — the same choice
    ``cimd``'s fetch makes by driving ``httpcore`` directly.

    ``resolver`` is injectable so the pin is testable without DNS.
    """

    def __init__(self, timeout_s: int = TIMEOUT_S, *, resolver=None) -> None:
        self.timeout_s = timeout_s
        self._resolver = resolver

    def post(self, url: str, body: bytes, headers: dict) -> tuple[int | None, str | None]:
        """Return ``(status_code, error)`` — exactly one of the two is ``None``.

        The candidate addresses share ONE ``timeout_s`` budget, so a hostile
        resolver cannot turn one delivery into ``MAX_PINNED_ADDRESSES`` connect
        timeouts. Stated exactly: the CONNECT phase is bounded by ``timeout_s`` in
        total. ``httpx``'s float timeout is a PER-PHASE timeout, so a candidate
        that completes its connect inside the budget may still spend one further
        read/write phase after the last deadline check — the same per-phase
        property the pre-pin ``httpx.post(timeout=...)`` had for a single target.
        """
        try:
            candidates = _pinned_targets(url, resolver=self._resolver)
        except ValueError as e:
            # A refused target is a RECEIPT, never a raise — same contract as a
            # transport failure, so the retry/backoff/disable path owns it.
            return None, redact_error(e)
        import httpx

        # Scrub any caller-supplied ``Host`` in ANY case spelling before setting
        # ours: two Host headers make h11 refuse the request outright, so a
        # stray lowercase key would turn the override into a hard delivery
        # failure instead of an override.
        out_headers = {k: v for k, v in headers.items() if k.lower() != "host"}
        # ``Host`` and ``sni_hostname`` are the same for every candidate — the
        # pin moves the ADDRESS only, never the name the subscriber's vhost and
        # its certificate are keyed on.
        host_header, sni = candidates[0][1], candidates[0][2]
        out_headers["Host"] = host_header
        extensions = {"sni_hostname": sni} if sni else None
        # ``trust_env=False`` keeps an env-configured proxy from re-resolving the
        # name and discarding the pin — but httpx reads ``SSL_CERT_FILE`` /
        # ``SSL_CERT_DIR`` ONLY when ``trust_env`` is on, so turning it off also
        # silently dropped the CA seam an operator-configured deployment relies
        # on. Restored EXPLICITLY here: a bundle supplied by env var still
        # verifies. Left UNSET otherwise, so an unset environment keeps httpx's
        # own default (and any ``verify`` a caller injects).
        cafile = os.environ.get("SSL_CERT_FILE")
        capath = os.environ.get("SSL_CERT_DIR")
        client_kwargs: dict = {"follow_redirects": False, "trust_env": False}
        if cafile or capath:
            client_kwargs["verify"] = ssl.create_default_context(
                cafile=cafile, capath=capath)
        # Connect-class failures move on to the NEXT vetted address; any other
        # error is the answer — a read-phase failure is not attributable to the
        # address (the peer, or a middlebox in front of it, answered), so walking
        # on to the untried candidates would not fix it.
        connect_errors = (httpx.ConnectError, httpx.ConnectTimeout)
        error: str | None = None
        # The ADDRESS CAP bounds the loop by COUNT; the budget bounds it by TIME.
        # Eight blackholed answers at the 10 s default would otherwise be ~80 s
        # of wall clock for ONE event, against ~10 s before the pin. Each attempt
        # gets what is left of one overall budget, and the loop stops when it is
        # gone.
        deadline = time.monotonic() + self.timeout_s
        for pinned_url in (candidate[0] for candidate in candidates):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                error = (
                    f"connect budget of {self.timeout_s}s exhausted before any "
                    f"vetted address answered"
                    + (f" (last error: {error})" if error else ""))
                break
            attempt_timeout = min(float(self.timeout_s), remaining)
            try:
                with httpx.Client(timeout=attempt_timeout,
                                  **client_kwargs) as client:
                    resp = client.post(pinned_url, content=body,
                                       headers=out_headers, extensions=extensions)
                return resp.status_code, None
            except connect_errors as e:
                error = redact_error(e)
                continue
            except Exception as e:
                return None, redact_error(e)
        return None, error or "no vetted address accepted the connection"


def _status_code(value) -> int | None:
    """Coerce a transport's status to an int, or ``None`` when unusable.

    Defensive on purpose: ``dispatch_pending`` promises never to raise, and a
    transport (or a subclass) that returns a non-numeric status would otherwise
    raise INSIDE the dispatch loop and escape to the dream worker as a failure.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _delivery_body(sub: dict, event: dict) -> bytes:
    envelope = {
        "subscription_id": sub["sub_id"],
        "delivery_id": f"{sub['sub_id']}:{int(event.get('seq') or 0)}",
        "event": {
            "event_id": event.get("event_id"),
            "seq": int(event.get("seq") or 0),
            "type": event.get("type"),
            "ts": event.get("ts"),
            "payload": event.get("payload"),
        },
    }
    return json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")


# ── Dispatcher ────────────────────────────────────────────────────────────

#: The ONE due predicate (#8098). The dispatcher reads it as a MATCH+RETURN
#: of the rows; the sweep reads it as a bare COUNT over a RAW graph handle (no
#: SDK, no index, no node — see ``due_subscription_count``). Two copies of this
#: string is the drift this constant exists to prevent.
#:
#: ⛔ TWO clauses, and BOTH are needed (#8098 review F3/P1). The temporal half
#: alone is TRUE for any subscription in its healthy steady state:
#: ``create_subscription`` writes no ``next_attempt_at`` and ``_mark_delivered``
#: resets it to NULL on every success, so "due by time" describes a subscription
#: for its entire life, not only while it owes something. Gating on it alone
#: therefore paid a full per-graph SDK open (plus the three
#: ``ensure_subscription_schema`` index statements) every interval for every
#: tenant that had EVER registered a webhook, whether or not a single event was
#: pending — the exact fleet-wide cost the sweep exists to bound.
_DUE_PREDICATE = (
    "s.disabled_at IS NULL "
    "AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= $now)"
)

#: The WORK half of the predicate, kept beside the temporal half so the two can
#: never drift (#8098 review F3). A subscription is only worth an SDK open when
#: it is behind the stream — ``s.cursor < $head`` means at least one event with
#: a higher ``seq`` EXISTS, i.e. ``read_after`` can return something (a delivery
#: that caught up has ``cursor == head`` and is a guaranteed no-op).
#:
#: THREE admission cases survive on purpose, and all are cheap no-ops the gate
#: cannot see without reading the stream itself:
#:
#: * a ``types``-filtered subscription whose pending events are all of a type it
#:   does not want (the filter is applied app-side in ``read_after``);
#: * a subscription whose unread events were PURGED (retention) while its
#:   cursor stayed behind — the counter's head then exceeds the store's max seq;
#: * a MISSED seq: ``next_seq`` handed a number out but nothing was appended
#:   under it (``sdk._emit_event`` calls ``next_seq`` then ``append_event`` in
#:   one ``try/except`` that logs and continues, and ``append_event`` also skips
#:   a duplicate ``event_id``). The counter then sits ABOVE
#:   ``max(:GraphEvent.seq)`` — the same SHAPE the purge produces, and the more
#:   common source of it, because it needs no retention window at all.
#:
#: ⛔ NONE of these is bounded by the retry ladder (#8098 review, non-counted):
#: the ladder disables a subscription only when a delivery FAILS, and none of
#: them ever fails a delivery. The FIRST is bounded by the TENANT (a
#: ``types``-filtered subscription is quiet for at most as long as no matching
#: event is emitted, so it resolves when the tenant's next matching event
#: arrives — not on a timer).
#:
#: The PURGED-CURSOR and MISSED-SEQ cases have NO bound at all: ``read_after``
#: returns ``[]``, so nothing is delivered and nothing
#: FAILS — no attempt accrues, no backoff is written, and nothing ever disables
#: the subscription inside ``max_attempts``. Their cursor stays below ``$head``
#: forever — the missed-seq graph heals only on that tenant's next SUCCESSFUL
#: emit, which for an idle tenant never comes — so the graph is admitted (one SDK
#: open, one no-op dispatch) on every interval, indefinitely. That is cheap but
#: unbounded, and it is stated here rather than papered over: the real remedy is
#: to advance such a cursor past ``:GraphEventMeta.first_seq`` (purge) or to the
#: store's max seq (missed) when ``read_after`` comes back empty — deliberately
#: NOT done in #8098, because silently advancing a cursor past events that
#: retention removed is a delivery-loss decision that belongs with the dispatch
#: state machine, not smuggled in as a cost optimisation.
_BEHIND_PREDICATE = "(s.cursor IS NULL OR s.cursor < $head)"


def event_head_seq(graph) -> int:
    """#8098: the highest ``:GraphEvent`` seq this graph has HANDED OUT (0 if none).

    The ``$head`` half of ``_BEHIND_PREDICATE``, and the reason the gate can
    answer "nothing owed" for a caught-up subscription. Takes a RAW graph handle
    (``proj.g`` or ``db.select_graph(name)``) for the same reason
    ``due_subscription_count`` does: the sweep must not open an SDK to ask.

    ONE node read, not a scan: every writer allocates through
    ``event_store.next_seq``, which MERGEs a single ``:GraphEventMeta`` node per
    graph and bumps ``last_seq`` there — so the head is a property of one node
    rather than an aggregate over the (unbounded, purged, millisecond-indexed)
    ``:GraphEvent`` label. That matters: the gate runs once per tenant graph per
    interval, so its cost has to stay flat.

    ``:GraphEventMeta`` ABSENT means nothing was ever allocated through the
    allocator — a graph that has never emitted. The fallback still asks the
    events themselves, because a store whose events predate the counter (a
    fixture, or a graph written by ``append_event`` directly) must not be
    STARVED by a gate that assumed "no counter ⇒ no events". ABSENT is one of
    TWO cases that reach the fallback (the other is a counter that is not a
    trustworthy position, below); a healthy counter keeps the steady state at
    the one-node read.

    A corrupt counter is treated EXACTLY like an absent one, and for the same
    reason: ``head`` is not the sweep's gate alone. The dispatcher threads it
    into ``_BEHIND_PREDICATE`` (``s.cursor < $head``), so a head of 0 answers
    "nothing owed" for every subscription on the graph — ``create_subscription``
    writes ``cursor: 0``, and ``0 < 0`` is false. Reading a corrupt counter as 0
    would therefore STARVE the graph's deliveries with no log line at all, which
    is the opposite of this gate's stated bias.

    "Corrupt" means a value ``_as_seq`` below will not accept as a POSITION. Note
    that this is not the set ``event_store.next_seq`` refuses on the write side —
    the two diverge at both ends, and the bullets below name both: anything that is
    not a real number (a numeric STRING included — ``next_seq`` is explicit that a
    property of another type is "refused, never coerced", because ``'5' + 1``
    cannot be compared), a NEGATIVE value, or a ``bool`` — which is an ``int``
    subclass, so ``int(True) == 1`` would otherwise pass as a trustworthy head of
    1 while the events run to 5. A non-integral float (``2.9``) is not a position
    either, and ``next_seq`` BUMPs it rather than refusing it. All of them take the
    events fallback, whose head is at or above every deliverable seq: it can only
    over-admit, never stall.

    ⚠️ COST. The fallback is the aggregate over ``:GraphEvent`` the opening
    paragraph exists to avoid on the steady path, so an UNTRUSTWORTHY graph pays it
    on every pass, and the counter does not become trustworthy on its own.

    ⛔ WHAT HAPPENS TO A BROKEN COUNTER IS NOT ONE STORY, so this paragraph reports
    only what the write side does, as measured. A reader who needs the repair
    story must measure the counter node:

    * ``next_seq`` REFUSES a non-number, a ``bool``, a negative counter and one
      at/above ``event_store.MAX_SEQ`` (which ``_as_seq`` below accepts
      deliberately) and leaves it unchanged — a negative or exhausted counter
      fails its ``prev >= 0 AND prev < $max`` grant test — so a graph holding one
      stops emitting until someone repairs the node. Only a non-integral float is
      BUMPED (``2.9`` → ``3.9``, re-issuing a seq an existing ``:GraphEvent``
      already holds — the #4653 class; #9059 records the ``bool``/numeric-string
      coercion below, while the ``2.9`` truncation is measured here).
    * a rebuild is NOT a repair path — it does both, by branch. Its pre-wipe
      ``capture_watermark`` reads through ``int()``, so anything ``int()`` ACCEPTS
      is COERCED (``'5'`` → ``5``, ``True`` → ``1``, ``2.9`` → ``2``) and the
      rebuild proceeds, leaving a position this gate TRUSTS but that is WRONG: a
      ``True`` counter comes back as ``1`` and a ``2.9`` as ``2``, both on a graph
      that had already issued seqs 1..3, so the next emit RE-ISSUES an
      already-issued seq (the #4653 class) — truncation LOWERS the head, so it is
      not a repair either. ``:GraphEvent`` rows are not replayed after a rebuild
      (#4664), so that ``1`` is below the seqs the graph HAD issued, not below any
      surviving row. And a value ``int()`` REJECTS (a non-numeric string) or whose
      ``int()`` is still outside ``0 .. MAX_SEQ`` makes that read RAISE, so the
      rebuild REFUSES: it aborts BEFORE the wipe and the counter is left untouched
      — the one outcome ``next_seq`` also gives, but reached by aborting a whole
      rebuild rather than by declining one seq. The domain test is on the
      CONVERTED value, so a stored NEGATIVE FRACTION (``-0.5``) folds to ``0``,
      passes, and is captured: MEASURED, the rebuild completes and the counter
      becomes a trusted ``0`` — the silent starvation the opening paragraphs rule
      out by name, arrived at through the rebuild. A counter at exactly
      ``MAX_SEQ`` is inside the domain too, so it is captured unchanged and the
      rebuild COMPLETES — and the graph still cannot emit, because ``next_seq``
      refuses it as exhausted: that one is neither coerced nor refused by the
      rebuild.

    ⛔ THE ONE CASE THIS FIX'S OVER-ADMISSION IS NOT A CHEAP NO-OP. Every
    unbounded admission ``_BEHIND_PREDICATE`` enumerates costs one SDK open and
    one no-op dispatch. A :GraphEvent row whose ``seq`` is FRACTIONAL is not that:
    the head here is ``ceil(max_seq)``, but ``_mark_delivered`` writes the cursor
    as ``int(seq)``, so the cursor can never reach the head. MEASURED on three
    events with the last rewritten to ``3.5``: head 4, pass 1 delivers 3
    (cursor → 3), and passes 2, 3, … each RE-DELIVER the same event, staying due
    forever. That is bounded only by the at-least-once contract (a re-post
    carries the same stable delivery id, so an idempotent consumer dedupes it),
    not by anything here. The remedy is the cursor write, tracked with the rest
    of it in #9032; no shipped writer mints a fractional ``seq``, so this needs a
    hand-written or restored row.

    A too-LARGE counter is left alone for that same asymmetry — it over-admits
    too, and clamping it would stall deliveries.
    """

    def _as_seq(raw) -> int | None:
        """A usable COUNTER value, or ``None`` when it must not be trusted.

        Strictly numeric: no string coercion (``event_store`` refuses a
        non-number rather than parsing it), no ``bool`` (an ``int`` subclass),
        no non-integral float. The LAST of those is this function's addition to
        ``next_seq``'s guard rather than a copy of it, and a counter at/above
        ``MAX_SEQ`` is accepted here although ``next_seq`` refuses it — both for
        the reason the outer docstring gives: over-admitting is the cheap side.
        """
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        if isinstance(raw, float) and not raw.is_integer():
            return None
        seq = int(raw)
        return seq if seq >= 0 else None

    def _events_head() -> int:
        """The events' own max seq — deliberately OVER-admitting, never strict.

        ⚠️ NOT ``_as_seq``, on purpose. Strictness is right for the counter and
        WRONG here: on this read a rejection IS the stall direction, because the
        value returned is the ``$head`` that decides whether a subscription is
        returned at all. A ``:GraphEvent.seq`` that is not an integer (a float
        from a hand-written or restored row) must still be rounded UP so
        ``read_after``'s payload is not filtered out by ``s.cursor < $head``;
        coercing a numeric string generously has the same justification. Only a
        value no number can be made of falls to 0.
        """
        rows = graph.query(
            "MATCH (e:GraphEvent) RETURN max(e.seq)").result_set
        raw = rows[0][0] if rows else None
        try:
            num = float(raw)
        except (TypeError, ValueError):
            return 0
        if not math.isfinite(num):
            return 0
        return max(0, math.ceil(num))

    rows = graph.query(
        "MATCH (m:GraphEventMeta) RETURN max(m.last_seq)").result_set
    head = _as_seq(rows[0][0] if rows else None)
    if head is None:
        return _events_head()
    return head


def due_subscription_count(graph, now: datetime | None = None,
                          head: int | None = None) -> int:
    """#8098: how many subscriptions this graph owes a delivery RIGHT NOW.

    THE SWEEP'S GATE, and the reason the sweep is affordable. It takes a RAW
    graph handle (``proj.g``, or ``db.select_graph(name)`` from the data-plane
    handle) rather than a projection, so the caller does not have to open an SDK
    — let alone a projection with its journal bind and embedder warm-up — to ask
    a question whose answer is "no" for every tenant that has never registered a
    subscription (i.e. almost all of them).

    Cost, MEASURED (embedded FalkorDB via a redislite server, 300 calls after
    warm-up, p50 / p95 ms): the COUNT read — which was the whole pre-fix gate —
    is 0.5/2.1 on an empty graph, 0.5–1.4/1.4–4.3 with 2000 unrelated
    ``:Filler`` nodes, 0.5–0.7/3.9–5.7 with 25 subscriptions (the per-graph cap,
    index present). The HEAD read (``event_head_seq``) is 0.6–1.9/3.7–9.8, and
    the FULL gate (both clauses, both reads) is 1.4–3.5/10.5–17.4. Two things
    that measurement is here to record: the cost is FLAT in graph SIZE (a label
    scan reads the label's node set, not the graph — which is also why the
    ``MAX_SUBSCRIPTIONS`` cap bounds it; the head is a property of ONE node) and
    it creates NOTHING (no index, no node — the p95 tail is the embedded store's
    own jitter, the same on every row, not a function of the read).

    FOR SCALE, the term the second clause REMOVES (same box, same script): one
    ``TortoiseSDK`` + ``_get_proj()`` + ``dispatch_pending`` on a graph whose
    subscription is caught up — the per-graph, per-interval cost the pre-fix
    always-true gate paid for every subscriber tenant, forever — measures
    **20.7 / 76.9 ms**. So the extra head read is ~1–2 ms spent to avoid a
    ~20 ms (p50) to ~77 ms (p95) SDK open that delivered nothing, and the
    ratio only grows with the journal bind and the embedder warm-up, neither of
    which the embedded measurement pays in full.

    The ``:ContextSubscription`` label has no index unless a subscription was
    created (``ensure_subscription_schema`` is the only place one is minted), so
    this read is the label scan it must be on a graph that has never held a
    subscription; a graph that HAS one carries the index.

    WHAT IT ANSWERS (#8098 review F3/P1): "does this graph owe a delivery right
    now" — a subscription that is due by TIME (``_DUE_PREDICATE``) **and** behind
    the stream (``_BEHIND_PREDICATE``). The second half is not an optimization:
    without it the gate is TRUE for the whole life of every healthy subscription
    (``_mark_delivered`` resets ``next_attempt_at`` to NULL), so every pass paid
    a full ``_make_sdk(graph_name=…)`` + ``_get_proj()`` (journal bind, embedder
    warm-up) for a dispatcher that then delivered nothing. ``head`` is read once
    per graph (``event_head_seq``) and may be passed in by a caller that already
    read it (``dispatch_pending`` does, so the two see ONE head).

    Never raises for a *state* problem — an unheld label reads as 0. A transport
    failure DOES propagate, and the caller treats that graph as "unknown, skip
    this pass" rather than "not due" (a failed read must not be read as "no").
    """
    if head is None:
        head = event_head_seq(graph)
    rows = graph.query(
        f"MATCH (s:ContextSubscription) WHERE {_DUE_PREDICATE} "
        f"AND {_BEHIND_PREDICATE} RETURN count(s)",
        params={"now": _iso(now or _now()), "head": int(head)},
    ).result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _due_subscriptions(proj, now: datetime,
                       head: int | None = None) -> list[dict]:
    """The dispatcher's half of the ONE predicate — same two clauses as the gate.

    The cursor clause is what makes the gate and the dispatcher answer the SAME
    question (#8098 review F3): a caught-up subscription is not returned here at
    all, so ``dispatch_pending`` no longer reads the stream once per caught-up
    subscription, and a graph whose subscriptions are all caught up returns
    BEFORE ``ensure_subscription_schema`` (three ``CREATE INDEX`` statements)
    like a graph with no subscriptions at all.
    """
    if head is None:
        head = event_head_seq(proj.g)
    rows = proj.g.query(
        "MATCH (s:ContextSubscription) "
        f"WHERE {_DUE_PREDICATE} AND {_BEHIND_PREDICATE} "
        "RETURN properties(s) ORDER BY s.created_at ASC",
        params={"now": _iso(now), "head": int(head)},
    ).result_set
    return [r[0] for r in rows]


def _mark_delivered(proj, sub: dict, event: dict, http_status: int | None,
                    now: datetime, duration_ms: int) -> None:
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.cursor=$cursor, "
        "s.attempts=0, s.next_attempt_at=NULL, s.delivered=s.delivered+1, "
        "s.last_error=NULL, s.last_delivery_at=$ts",
        params={"id": sub["sub_id"], "cursor": int(event.get("seq") or 0),
                "ts": _iso(now)},
    )
    _write_receipt(proj, sub_id=sub["sub_id"], event=event,
                   attempt=int(sub.get("attempts") or 0) + 1, status="delivered",
                   http_status=http_status, error=None, now=now,
                   duration_ms=duration_ms)


def _mark_failed(proj, sub: dict, event: dict, http_status: int | None,
                 error: str | None, now: datetime, duration_ms: int) -> bool:
    """Record a failed attempt. Returns True when the subscription got DISABLED."""
    attempt = int(sub.get("attempts") or 0) + 1
    limit = max_attempts()
    disabled = attempt >= limit
    # The disable stamp is part of the DISABLED branch only: this pass only
    # dispatches subscriptions whose `disabled_at` is NULL, so a non-terminal
    # failure must leave the property ALONE rather than write an explicit NULL
    # (a `SET ...=NULL` here would silently un-disable a subscription an
    # unrelated writer disabled).
    disable_clause = (
        "s.disabled_at=$ts, s.disabled_reason=$reason" if disabled else "").rstrip()
    reason = (f"{limit} consecutive delivery failures — last: "
              f"{error or f'HTTP {http_status}'}") if disabled else None
    # `last_delivery_at` is deliberately NOT touched here: it records the last
    # SUCCESSFUL delivery (the read path reports it as such). A failed attempt
    # is recorded on the receipt, where the status and the error live.
    proj.g.query(
        "MATCH (s:ContextSubscription {sub_id:$id}) SET s.attempts=$attempt, "
        "s.next_attempt_at=$next, s.failed=s.failed+1, s.last_error=$err"
        + (", " + disable_clause if disable_clause else ""),
        params={"id": sub["sub_id"], "attempt": attempt,
                "next": None if disabled else _iso(
                    now + timedelta(seconds=backoff_seconds(attempt))),
                "err": (error or f"HTTP {http_status}")[:500],
                "reason": reason, "ts": _iso(now)},
    )
    _write_receipt(proj, sub_id=sub["sub_id"], event=event, attempt=attempt,
                   status="exhausted" if disabled else "retrying",
                   http_status=http_status, error=error, now=now,
                   duration_ms=duration_ms)
    return disabled


def dispatch_pending(proj, *, transport=None, now: datetime | None = None,
                     batch: int = DEFAULT_BATCH) -> dict:
    """Push pending events to every due subscription. NEVER raises.

    Three triggers, one predicate (#8098): the end of a tenant's dream cycle
    (``hosted_api._dream_worker`` — the cadence for NEW events), the SCHEDULED
    RETRY SWEEP (``hosted_api._webhook_retry_sweep_pass`` — the trigger for a
    retry whose backoff elapsed while the tenant stayed idle), and on demand.
    Each subscription is drained IN ORDER from its cursor; the
    first failure stops that subscription's batch (head-of-line ordering is
    what makes the at-least-once contract re-deliverable) and schedules the
    retry. Returns a summary dict for logging/tests:

    ``{"enabled", "subscriptions", "delivered", "failed", "disabled", "more_pending"}``
    """
    summary = {"enabled": False, "subscriptions": 0, "delivered": 0,
               "failed": 0, "disabled": 0, "more_pending": 0}
    if not dispatch_enabled():
        return summary
    summary["enabled"] = True
    transport = transport or WebhookTransport()
    now_dt = now or _now()
    try:
        # The due query runs FIRST, before ``ensure_subscription_schema``: a
        # graph with no subscriptions (i.e. every existing deployment, and
        # every test that drives a dream) must take this path WITHOUT creating
        # an index. Index creation is a graph WRITE, and a delivery pass that
        # has nothing to deliver should leave the graph byte-identical.
        # "Nothing to deliver" includes "every subscription is caught up" —
        # `_due_subscriptions` carries the ``cursor < head`` clause, so the
        # head is read ONCE here and threaded into the query (#8098 review F3).
        head = event_head_seq(proj.g)
        subs = _due_subscriptions(proj, now_dt, head=head)
    except Exception as e:
        logger.warning("webhook_delivery: subscription read failed: %s",
                       redact_error(e))
        return summary
    if not subs:
        return summary
    ensure_subscription_schema(proj)
    from .crypto import decrypt_token
    from .event_store import read_after

    for sub in subs:
        summary["subscriptions"] += 1
        try:
            types = list(sub.get("types") or []) or None
            events = read_after(proj, int(sub.get("cursor") or 0), types=types,
                                limit=max(1, min(int(batch), 1000)))
        except Exception as e:
            logger.warning("webhook_delivery: event read failed for %s: %s",
                           sub.get("sub_id"), redact_error(e))
            continue
        if len(events) >= max(1, int(batch)):
            summary["more_pending"] += 1
        if not events:
            continue
        try:
            secret = decrypt_token(sub.get("secret") or "")
        except Exception as e:
            logger.error("webhook_delivery: cannot decrypt secret for %s: %s",
                         sub.get("sub_id"), redact_error(e))
            _mark_failed(proj, sub, events[0], None,
                         "subscription secret unreadable (encryption key "
                         "rotated or unset)", now_dt, 0)
            continue
        for event in events:
            started = time.monotonic()
            body = _delivery_body(sub, event)
            ts = int(now_dt.timestamp())
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "tortoise-webhook/1",
                "X-Tortoise-Delivery": f"{sub['sub_id']}:{int(event.get('seq') or 0)}",
                "X-Tortoise-Event": str(event.get("type") or ""),
                "X-Tortoise-Seq": str(int(event.get("seq") or 0)),
                "X-Tortoise-Signature": signature_header(secret, ts, body),
            }
            try:
                http_status, error = transport.post(sub["url"], body, headers)
            except Exception as e:  # a transport that RAISES is a failed delivery
                http_status, error = None, redact_error(e)
            http_status = _status_code(http_status)
            if error is None and http_status is None:
                error = "transport returned no usable status"
            duration_ms = int((time.monotonic() - started) * 1000)
            if error is None and 200 <= http_status < 300:
                try:
                    _mark_delivered(proj, sub, event, http_status, now_dt, duration_ms)
                except Exception as e:
                    logger.warning("webhook_delivery: cursor write failed for %s: %s",
                                   sub.get("sub_id"), redact_error(e))
                summary["delivered"] += 1
                continue
            summary["failed"] += 1
            try:
                if _mark_failed(proj, sub, event, http_status, error, now_dt,
                                duration_ms):
                    summary["disabled"] += 1
            except Exception as e:
                logger.warning("webhook_delivery: failure write failed for %s: %s",
                               sub.get("sub_id"), redact_error(e))
            break  # head-of-line: retry THIS event first on the next pass
    return summary
