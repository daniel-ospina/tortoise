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
   Delivery runs at the END of a tenant's dream cycle (``hosted_api``
   ``_dream_worker``) and on demand; it is never awaited by a mutation.
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
import os
import ssl
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit

from tortoise.env_truthy import FALSY

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

def _due_subscriptions(proj, now: datetime) -> list[dict]:
    rows = proj.g.query(
        "MATCH (s:ContextSubscription) "
        "WHERE s.disabled_at IS NULL "
        "AND (s.next_attempt_at IS NULL OR s.next_attempt_at <= $now) "
        "RETURN properties(s) ORDER BY s.created_at ASC",
        params={"now": _iso(now)},
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

    Runs at the end of a tenant's dream cycle (``hosted_api._dream_worker``)
    and on demand. Each subscription is drained IN ORDER from its cursor; the
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
        subs = _due_subscriptions(proj, now_dt)
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
