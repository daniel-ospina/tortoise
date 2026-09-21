"""Client ID Metadata Document (CIMD) client identity for the hosted MCP
authorization server (#2847).

References
----------
* ``draft-ietf-oauth-client-id-metadata-document-00`` — ``client_id`` is a URL
  the authorization server dereferences for client metadata.
* Anthropic's connector-authentication docs — Claude selects CIMD only when the
  AS metadata advertises BOTH ``client_id_metadata_document_supported: true``
  and ``"none"`` in ``token_endpoint_auth_methods_supported``; and the consent
  screen MUST show the client_id **host**, never the self-asserted
  ``client_name``.
* #2866 — the bounded DCR capacity policy this complements.
* #2846 — the loopback port-agnostic redirect matcher reused here.

Why this module exists
----------------------
Advertising CIMD removes the ``POST /register`` round-trip that Anthropic warns
about at directory scale (DCR registers a fresh client on every fresh
connection). The price is that ``client_id`` becomes an **attacker-supplied URL
the authorization server fetches on the unauthenticated /oauth/authorize path**
— a server-side request forgery surface. Every control the issue enumerates is
implemented here:

1. **URL validation** (:func:`validate_client_id_url`) — https only, absolute,
   path component present, no userinfo, no fragment, no literal *or*
   percent-encoded dot segments, length + control-character caps.
2. **Host validation** (:func:`public_addresses` via :func:`is_public_address`)
   — every address the host resolves to must be globally routable: no private,
   loopback, link-local, carrier-grade-NAT, multicast, reserved, unspecified or
   NAT64-embedded destination.
3. **Redirect policy** — ``httpcore`` never follows redirects, and a 3xx is a
   hard failure here, so no hop is ever taken to an unvalidated host.
4. **Size + timeout caps** — the body is read through a byte cap and the whole
   exchange carries connect/read timeouts.
5. **Fetch cache** — successful, well-formed documents only, TTL'd and
   LRU-bounded. Errors and malformed documents are NEVER cached (spec §4.3:
   "MUST NOT cache error responses" / "MUST NOT cache documents which are
   invalid or malformed").
6. **Fetch rate limiting** — per-host + aggregate + store cap, mirroring the
   DCR limiter's bucket idiom in ``hosted_api``.
7. **Total occupancy** (#3669) — a process-wide in-flight cap, a per-fetch
   deadline bounding every SOCKET phase (connect loop, TLS, status/header and
   body reads; the per-op timeouts do not bound a trickled response), and a
   wall-clock budget per window whose worst case is RESERVED at admission, so
   the product (fetches x duration) is bounded and not just the fetch count.
   The one unbounded tail is the OS resolver's own ``getaddrinfo`` timeout
   (pre-existing, not attacker-settable, and still bounded in aggregate by the
   in-flight cap and the budget).

Control (2) is closed against **DNS rebinding** by connecting the TCP socket to
the *validated* address while TLS SNI and the HTTP ``Host`` header stay on the
hostname (``httpcore`` passes ``server_hostname=origin.host`` to
``start_tls``). A resolve-then-hand-the-name-to-the-HTTP-client design leaves a
TOCTOU window in which the name can re-resolve to an internal address between
validation and connect; pinning the socket removes the window instead of
narrowing it.

Accepted limitations (mirrored in the PR body): the rate-limit and fetch-cache
stores are in-process, so the real bound is ``limit × running machines`` and
resets on restart — the same accepted limitation as ``_OAUTH_DCR_BUCKETS``
(#2866, whose follow-up filings cover the shared primitive). The fetch is
synchronous, which matches the existing control-plane call style on this path
(``cp.query`` is a blocking PostgREST call made from the same async handler);
#3669 runs the WHOLE resolution off the event loop through the bounded
``monitoring`` offload seam, so the synchronicity no longer occupies the loop.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from collections import OrderedDict
from urllib.parse import unquote, urlparse

import httpcore

from .env_truthy import env_flag  # #4097: the declared truthy contract

__all__ = [
    "CimdError",
    "client_id_metadata_document_supported",
    "fetch_client_metadata",
    "is_cimd_client_id",
    "is_public_address",
    "resolve_client_metadata",
    "validate_client_id_url",
]

# ── Caps (module constants, kept deliberately strict; the two FEATURE levers
#    below are the env-overridable surface) ─────────────────────────────────

CLIENT_ID_MAX_LEN = 2048
DOCUMENT_MAX_BYTES = 64 * 1024
CONNECT_TIMEOUT_S = 3.0
READ_TIMEOUT_S = 3.0
MAX_ADDRESSES = 8

CACHE_TTL_S = 300
CACHE_CAP = 128

RATE_WINDOW_S = 3600
PER_HOST_PER_HOUR = 60
AGGREGATE_PER_HOUR = 600
STORE_CAP = 256

# ── Control 7 — TOTAL OCCUPANCY (#3669) ─────────────────────────────────────
# The rate limiter above bounds fetch COUNT. It does not bound the PRODUCT:
# the cache key is the full ``client_id`` URL, so varying the path/query yields
# unlimited distinct keys against the shared aggregate, and at the 3 s connect
# + 3 s read ceiling 600 fetches is up to ~3600 s of work in a 3600 s window —
# the whole window, on one event loop (`Dockerfile.hosted` runs one uvicorn
# process with no ``--workers``). These bounds cap the product instead:
# at most ``MAX_IN_FLIGHT_FETCHES`` fetches run at once, and a window may spend
# at most ``FETCH_BUDGET_S`` seconds inside fetches. They are charged inside
# :func:`resolve_client_metadata`, which every unauthenticated front door
# reaches through ``resolve_client`` (`/oauth/authorize`, `/oauth/consent`, and
# both `/oauth/token` grants via ``_verify_client_auth``) — so no door can
# escape the accounting.
MAX_IN_FLIGHT_FETCHES = 4
FETCH_BUDGET_S = 120.0
#: Wall-clock deadline for ONE fetch, across every SOCKET phase
#: (``READ_TIMEOUT_S`` is a per-socket-read timeout, not a total one, so a server
#: that trickles the
#: status line, the headers, or the body keeps resetting it; ``_DeadlineStream``
#: caps each read/write/TLS timeout by the remaining deadline (and
#: ``_PinningNetworkBackend`` caps each connect attempt), so the CONNECT loop,
#: TLS handshake, header read and body read are all bounded. The budget RESERVES
#: this at admission (see ``_budget_reserve``), so a fetch that never returns
#: cannot be reset out of the accounting. Kept BELOW
#: ``monitoring.CONTROL_PLANE_OFFLOAD_TIMEOUT_S`` so a fetch returns before its
#: caller's offload bound. The OS resolver's own timeout on ``getaddrinfo`` is
#: the one unbounded tail (pre-existing; see the module docstring).
FETCH_MAX_S = 6.0
#: How long a resolution waits for an in-flight slot before refusing. Bounded,
#: so a saturated cap fails fast instead of parking a caller indefinitely.
IN_FLIGHT_WAIT_S = 2.0

# Deprecated special-purpose ranges that Python's `is_global` still reports as
# global (#2847 review P2): 6to4 relay anycast (RFC 7526) and IPv6 site-local
# (RFC 3879). Effectively unroutable, but the predicate claims "globally
# routable" so it should not admit them.
_IPV4_6TO4_RELAY = ipaddress.ip_network("192.88.99.0/24")


class CimdError(Exception):
    """A CIMD fetch or validation refusal. Never caches, always fail-closed."""


def cimd_enabled() -> bool:
    """Is CIMD advertised and accepted? Default ON (#2847 indicator); the env
    is the reversible lever, not a deployment.

    #4097: through the declared truthy contract. NOTE the deliberate change — a
    BLANK value (``TORTOISE_OAUTH_CIMD=``) is now *unset* (→ default ON) rather
    than an explicit off; ``0``/``false``/``no``/``off`` still disable it.
    """
    return env_flag("TORTOISE_OAUTH_CIMD", True)


def same_origin_redirects_required() -> bool:
    """Require non-loopback ``redirect_uris`` to be same-origin with the
    client_id URL. Default ON (Anthropic's own guidance for CIMD). This is the
    single reversible lever if a future client's document legitimately spans
    hosts — see ``docs/oauth-mcp.md``.

    #4097: through the declared truthy contract. A BLANK value is now *unset*
    (→ default ON = required); before #4097 blank relaxed this guard, which was
    the fail-OPEN direction.
    """
    return env_flag("TORTOISE_OAUTH_CIMD_SAME_ORIGIN", True)


def client_id_metadata_document_supported() -> bool:
    """The AS-metadata flag value."""
    return cimd_enabled()


def is_cimd_client_id(client_id: object) -> bool:
    """Cheap pre-filter: does this look like a CIMD client_id at all? Only an
    https URL can be one, so registry lookups for ``ct_…`` ids never touch the
    fetch path."""
    return isinstance(client_id, str) and client_id.startswith("https://")


# ── Control 1 — client_id URL validation ───────────────────────────────────

_DOT_SEGMENTS = {".", ".."}


def _has_dot_segment(path: str) -> bool:
    """A literal *or* percent-encoded ``.``/``..`` path segment.

    The spec bans the literal form; the encoded form matters because an origin
    that decodes before routing sees `%2e%2e%2f` as a separator, which
    `urlsplit` does NOT — so `https://h/a/%2e%2e%2fb` is one segment to us and
    `../b` to them. Decoding to a fixed point (bounded — `%25` can nest) covers
    single- and double-encoded separators rather than only whole-segment
    encodings (#2847 review P2).
    """
    seen = path
    for _ in range(4):
        if any(seg in _DOT_SEGMENTS for seg in seen.split("/")):
            return True
        decoded = unquote(seen)
        if decoded == seen:
            break
        seen = decoded
    return False


def validate_client_id_url(client_id: object) -> str:
    """Validate a CIMD ``client_id`` and return it **unchanged**.

    Returned verbatim, not normalized: §4.1 requires the document's own
    ``client_id`` to match the URL by *simple string comparison*, so
    normalizing here would break the self-reference check for a client that
    spelled its own URL the way it sent it.
    """
    if not isinstance(client_id, str) or not client_id:
        raise CimdError("client_id must be a non-empty string.")
    if len(client_id) > CLIENT_ID_MAX_LEN:
        raise CimdError("client_id is too long.")
    if any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in client_id):
        raise CimdError("client_id must not contain whitespace or control characters.")
    try:
        parts = urlparse(client_id)
        hostname = parts.hostname
    except ValueError as exc:
        raise CimdError("client_id is not a parsable URL.") from exc
    if parts.scheme != "https":
        raise CimdError("client_id must use the https scheme.")
    if not hostname:
        raise CimdError("client_id must have a host.")
    if not hostname.isascii():
        # #2847 review P2: the consent screen shows the HOST, so a non-ASCII
        # (IDN) host is a homograph — `сlaude.ai` with a Cyrillic с is
        # visually identical to `claude.ai`. Requiring the punycode (A-label)
        # form makes the displayed identity unambiguous.
        raise CimdError(
            "client_id host must be an ASCII (punycode) name.")
    if parts.username is not None or parts.password is not None:
        raise CimdError("client_id must not contain a username or password.")
    if parts.fragment:
        raise CimdError("client_id must not contain a fragment.")
    if not parts.path or parts.path == "/":
        raise CimdError("client_id must contain a path component.")
    if _has_dot_segment(parts.path):
        raise CimdError("client_id path must not contain . or .. segments.")
    return client_id


# ── Control 2 — host validation (every resolved address must be public) ─────


def is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Is ``ip`` a globally routable destination?

    ``is_global`` alone is not enough: ``224.0.0.1`` (multicast), NAT64's
    ``64:ff9b::/96`` and the deprecated 6to4-relay / site-local ranges all
    report ``is_global=True``. An IPv4-mapped IPv6 address is judged by the
    IPv4 address it embeds, so ``::ffff:127.0.0.1`` cannot smuggle a loopback
    target past the check.
    """
    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
        elif ip.is_site_local:                  # fec0::/10 — RFC 3879
            return False
    if ip.version == 4 and ip in _IPV4_6TO4_RELAY:   # RFC 7526
        return False
    return bool(ip.is_global
                and not ip.is_multicast
                and not ip.is_reserved
                and not ip.is_unspecified)


def public_addresses(host: str, port: int) -> list[str]:
    """Resolve ``host`` and return its addresses **only if every one is
    public** — a host that resolves to a mix of public and private addresses is
    refused outright rather than filtered, so an attacker cannot win by
    publishing one public A record beside a private one."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise CimdError(f"could not resolve client_id host {host!r}.") from exc
    addresses: list[str] = []
    for info in infos[:MAX_ADDRESSES]:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise CimdError("client_id host resolved to an unparsable address.") from exc
        if not is_public_address(ip):
            raise CimdError(
                "client_id host resolves to a non-public address "
                f"({ip}) — refusing to fetch.")
        text = str(ip)
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise CimdError(f"client_id host {host!r} did not resolve.")
    return addresses


class _DeadlineStream(httpcore.NetworkStream):
    """Wrap a ``NetworkStream`` so EVERY blocking op honours an absolute deadline.

    #3669 review: ``READ_TIMEOUT_S`` is a per-socket-read timeout, so a server
    that trickles the STATUS LINE, the HEADERS, or the body resets it on every
    byte and can hold a worker — and an in-flight permit — indefinitely. The
    body alone was deadline-checked, and ``httpcore``'s ``close()`` cannot
    interrupt a blocked ``recv``, so the header phase was unbounded. Capping
    each ``read``/``write``/``start_tls`` timeout by the deadline's remainder
    (and refusing once it is spent) is what actually bounds the whole exchange.
    """

    def __init__(self, inner, deadline: float) -> None:
        self._inner = inner
        self._deadline = deadline

    def _bound(self, timeout: float | None, exc: type[Exception]) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise exc("CIMD fetch deadline exceeded.")
        return remaining if timeout is None else min(timeout, remaining)

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(
            max_bytes, timeout=self._bound(timeout, httpcore.ReadTimeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        return self._inner.write(
            buffer, timeout=self._bound(timeout, httpcore.WriteTimeout))

    def close(self) -> None:
        return self._inner.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        inner = self._inner.start_tls(
            ssl_context, server_hostname=server_hostname,
            timeout=self._bound(timeout, httpcore.ConnectTimeout))
        return _DeadlineStream(inner, self._deadline)

    def get_extra_info(self, info: str):
        return self._inner.get_extra_info(info)


class _PinningNetworkBackend(httpcore.NetworkBackend):
    """``httpcore`` network backend that connects to the **validated** address.

    ``httpcore`` calls ``connect_tcp(host=origin.host)`` with the URL's
    hostname and then ``start_tls(server_hostname=origin.host)``, so swapping
    the TCP destination for the address we just vetted leaves certificate
    verification and SNI on the hostname. That is what makes the pin meaningful:
    the socket can never land on an address that ``public_addresses`` did not
    approve, even if DNS changes between the two calls.
    """

    def __init__(self, inner: httpcore.NetworkBackend | None = None,
                 deadline: float | None = None) -> None:
        self._inner = inner if inner is not None else httpcore.SyncBackend()
        # An ABSOLUTE ``time.monotonic()`` deadline for the whole fetch, or None
        # for the historical unbounded connect (tests and any non-fetch use).
        self._deadline = deadline

    def connect_tcp(self, host, port, timeout=None, local_address=None,
                    socket_options=None):
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise httpcore.ConnectTimeout(
                f"CIMD fetch deadline exceeded before resolving {host!r}")
        addresses = public_addresses(host, int(port))
        last_error: Exception | None = None
        for address in addresses:
            remaining = (None if self._deadline is None
                         else self._deadline - time.monotonic())
            if remaining is not None and remaining <= 0:
                # #3669 review: httpcore's own ``close()`` cannot interrupt a
                # blocked connect (the connection object does not exist yet), so
                # the connect LOOP must honour the deadline itself — otherwise a
                # host with several blackholed addresses burns
                # CONNECT_TIMEOUT_S each.
                raise httpcore.ConnectTimeout(
                    f"CIMD fetch deadline exceeded before connecting to {host!r}")
            address_timeout = timeout
            if remaining is not None:
                address_timeout = (remaining if timeout is None
                                   else min(timeout, remaining))
            try:
                stream = self._inner.connect_tcp(
                    address, port, timeout=address_timeout,
                    local_address=local_address, socket_options=socket_options)
            except Exception as exc:            # try the next vetted address
                last_error = exc
                continue
            # Every later phase (TLS, status line, headers, body) goes through
            # the same stream, so the deadline must travel with it.
            if self._deadline is None:
                return stream
            return _DeadlineStream(stream, self._deadline)
        raise httpcore.ConnectError(
            f"no vetted address for {host!r} accepted the connection"
        ) from last_error

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise httpcore.ConnectError(
            "unix sockets are not an acceptable CIMD fetch destination.")


# ── Control 6 — fetch rate limiting ────────────────────────────────────────

_RATE_BUCKETS: OrderedDict[str, list[float]] = OrderedDict()
_RATE_AGGREGATE: list[float] = []
_RATE_LOCK = threading.Lock()

#: Control 7 state (#3669). ``_IN_FLIGHT`` caps concurrent fetches; the budget
#: accumulates ACTUAL fetch seconds and resets at the window boundary. Both are
#: reset by :func:`_rate_limit_reset`, the single test seam for the limiter
#: family, so test ordering cannot leak across either store.
_IN_FLIGHT = threading.BoundedSemaphore(MAX_IN_FLIGHT_FETCHES)
_BUDGET_LOCK = threading.Lock()
_BUDGET_STARTED = time.monotonic()
_BUDGET_SPENT = 0.0


def _rate_limit_reset() -> None:
    """Test seam — the stores are in-process module state."""
    global _IN_FLIGHT, _BUDGET_STARTED, _BUDGET_SPENT
    with _RATE_LOCK:
        _RATE_BUCKETS.clear()
        _RATE_AGGREGATE.clear()
    with _BUDGET_LOCK:
        _BUDGET_STARTED = time.monotonic()
        _BUDGET_SPENT = 0.0
    # A FRESH semaphore, not a set of released permits: a test (or a real
    # wedged fetch) that consumed a slot must not shrink the next window's cap.
    _IN_FLIGHT = threading.BoundedSemaphore(MAX_IN_FLIGHT_FETCHES)


def _budget_remaining_locked(now: float) -> float:
    """Fetch seconds left in the current window, rolling it over in place.

    Caller holds ``_BUDGET_LOCK``. The roll-over is lazy (on the next check)
    rather than a timer, so there is no background thread and no clock to
    reconcile: the first call after the window elapses starts the new one.
    """
    global _BUDGET_STARTED, _BUDGET_SPENT
    if now - _BUDGET_STARTED >= RATE_WINDOW_S:
        _BUDGET_STARTED = now
        _BUDGET_SPENT = 0.0
    return FETCH_BUDGET_S - _BUDGET_SPENT


def _budget_reserve() -> float:
    """Reserve ``FETCH_MAX_S`` against the CURRENT window; return its start time.

    Reserving the WORST CASE up front (rather than charging the actual duration
    after the fact) is what makes the window budget a true upper bound:
    concurrent admissions cannot overshoot it, and a fetch that is ABANDONED
    (its caller's offload bound expired while the worker kept running) keeps its
    full reservation charged until it finishes. The returned window start is the
    reservation's GENERATION, which :func:`_budget_settle` needs so a
    cross-window settle cannot refund a reservation the new window already
    reset (that would erase a live reservation made there).
    """
    now = time.monotonic()
    global _BUDGET_SPENT
    with _BUDGET_LOCK:
        if _budget_remaining_locked(now) < FETCH_MAX_S:
            raise CimdError(
                "CIMD fetch wall-clock budget exhausted for this window.")
        _BUDGET_SPENT += FETCH_MAX_S
        return _BUDGET_STARTED


def _budget_settle(elapsed: float, window: float) -> None:
    """Replace the ``FETCH_MAX_S`` reservation with the ACTUAL elapsed time.

    Called in a ``finally``, so a failed fetch still pays for the time it
    occupied. ``window`` is the start time returned by :func:`_budget_reserve`:
    when it no longer matches the current window, the reservation it belonged to
    was ALREADY reset by the roll-over, so this settle must not subtract a
    second time (which would credit the window up to a full reservation and
    cancel a LIVE reservation made there) — it charges the actual elapsed
    instead. Same-window settles replace the reservation, clamped at zero so a
    negative ``_BUDGET_SPENT`` can never reopen the budget.
    """
    global _BUDGET_SPENT
    with _BUDGET_LOCK:
        if window != _BUDGET_STARTED:
            _BUDGET_SPENT += elapsed
            return
        _BUDGET_SPENT = max(0.0, _BUDGET_SPENT + elapsed - FETCH_MAX_S)


def _prune(bucket: list[float], now: float) -> list[float]:
    return [t for t in bucket if now - t < RATE_WINDOW_S]


def _charge_rate_limit(host: str) -> None:
    """Charge one fetch against ``host`` and the aggregate. Mirrors
    ``_check_oauth_dcr_rate_limit``'s charging doctrine (charge at CHECK time),
    with the same documented consequence: a control-plane failure after the
    charge still consumes budget."""
    now = time.monotonic()
    with _RATE_LOCK:
        global _RATE_AGGREGATE
        _RATE_AGGREGATE = _prune(_RATE_AGGREGATE, now)
        if len(_RATE_AGGREGATE) >= AGGREGATE_PER_HOUR:
            raise CimdError("CIMD fetch rate limit reached (aggregate).")
        bucket = _prune(_RATE_BUCKETS.get(host, []), now)
        if len(bucket) >= PER_HOST_PER_HOUR:
            raise CimdError("CIMD fetch rate limit reached for this host.")
        while host not in _RATE_BUCKETS and len(_RATE_BUCKETS) >= STORE_CAP:
            _RATE_BUCKETS.popitem(last=False)
        bucket.append(now)
        _RATE_BUCKETS[host] = bucket
        _RATE_BUCKETS.move_to_end(host)
        _RATE_AGGREGATE.append(now)


# ── Control 5 — fetch cache (successes only) ───────────────────────────────

_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _cache_reset() -> None:
    """Test seam."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(client_id: str) -> dict | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(client_id)
        if entry is None:
            return None
        stored_at, document = entry
        if now - stored_at >= CACHE_TTL_S:
            _CACHE.pop(client_id, None)
            return None
        _CACHE.move_to_end(client_id)
        return document


def _cache_put(client_id: str, document: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[client_id] = (time.monotonic(), document)
        _CACHE.move_to_end(client_id)
        while len(_CACHE) > CACHE_CAP:
            _CACHE.popitem(last=False)


# ── Controls 3 + 4 — the fetch itself ──────────────────────────────────────

def _read_capped(stream, cap: int, deadline: float | None = None) -> bytes:
    body = bytearray()
    for chunk in stream:
        if deadline is not None and time.monotonic() > deadline:
            raise CimdError(
                "client metadata document exceeded the fetch deadline.")
        if not chunk:
            continue
        body.extend(chunk)
        if len(body) > cap:
            raise CimdError("client metadata document exceeds the size cap.")
    return bytes(body)


def fetch_client_metadata(client_id: str) -> dict:
    """Fetch the metadata document at a validated ``client_id`` URL.

    No redirects are followed (``httpcore`` does not follow any; a 3xx is
    refused explicitly), the body is byte-capped, and the socket connects only
    to an address ``public_addresses`` approved. Returns the parsed JSON
    object; raises :class:`CimdError` on anything else.
    """
    if not urlparse(client_id).hostname:      # already validated by the caller
        raise CimdError("client_id must have a host.")
    # A proxy is deliberately NOT honoured: it would move the egress off the
    # pinned socket and hand the destination back to a name, defeating (2).
    timeout = {"connect": CONNECT_TIMEOUT_S, "read": READ_TIMEOUT_S,
               "write": CONNECT_TIMEOUT_S, "pool": CONNECT_TIMEOUT_S}
    deadline = time.monotonic() + FETCH_MAX_S
    pool = httpcore.ConnectionPool(
        network_backend=_PinningNetworkBackend(deadline=deadline),
        max_connections=2, retries=0)
    try:
        # The validated URL goes to httpcore verbatim: it derives SNI and the
        # Host header from the URL's host, which is exactly the pairing the
        # pinning backend relies on (socket → vetted IP, TLS name → hostname).
        with pool, pool.stream(
                "GET", client_id,
                headers=[(b"accept", b"application/json"),
                         (b"user-agent", b"tortoise-mcp-cimd/1")],
                extensions={"timeout": timeout}) as response:
            status = response.status
            if 300 <= status < 400:
                raise CimdError(
                    "client_id URL redirected — redirects are not followed.")
            if status != 200:
                raise CimdError(
                    f"client_id URL returned HTTP {status}.")
            body = _read_capped(response.iter_stream(), DOCUMENT_MAX_BYTES,
                                deadline=deadline)
    except CimdError:
        raise
    except Exception as exc:
        raise CimdError(f"could not fetch the client metadata document: {exc}") from exc
    try:
        document = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise CimdError("client metadata document is not valid UTF-8 JSON.") from exc
    if not isinstance(document, dict):
        raise CimdError("client metadata document must be a JSON object.")
    return document


# ── Document semantics (spec §4.1 + Anthropic's consent-screen rule) ───────


def _same_origin(a, b) -> bool:
    """Scheme + host + effective-port equality, so ``https://x`` and
    ``https://x:443`` are the same origin."""
    def key(parsed):
        scheme = (parsed.scheme or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80 if scheme == "http" else None)
        return scheme, (parsed.hostname or "").lower(), port
    return key(a) == key(b)


def validate_document(client_id: str, document: dict,
                      *, supported_scopes: set[str],
                      supported_grants: set[str],
                      default_scope: str) -> dict:
    """Validate a fetched document and return the normalized client record.

    Reuses ``oauth._valid_redirect_uri`` and ``oauth._is_loopback`` so the
    redirect rules are identical to the registry path rather than a second,
    drifting implementation (``_redirect_uri_matches`` applies the
    port-agnostic loopback rule later, at authorize time).
    """
    from tortoise.oauth import _is_loopback, _valid_redirect_uri

    declared = document.get("client_id")
    if not isinstance(declared, str) or declared != client_id:
        raise CimdError(
            "client metadata document is not self-referential "
            "(client_id does not match the URL it was served from).")

    method = document.get("token_endpoint_auth_method", "none")
    if method != "none":
        # §4.1: a CIMD client cannot establish a shared secret, so
        # client_secret_post/_basic MUST be refused.
        raise CimdError(
            "client metadata document must use token_endpoint_auth_method 'none'.")

    redirect_uris = document.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise CimdError("client metadata document must declare redirect_uris.")
    client_url = urlparse(client_id)
    require_same_origin = same_origin_redirects_required()
    for uri in redirect_uris:
        if not _valid_redirect_uri(uri):
            raise CimdError(
                "each redirect_uri must be https, or http on a loopback host.")
        parsed = urlparse(uri)
        if _is_loopback(parsed.hostname or ""):
            # Native clients (Claude Code) declare a loopback listener on an
            # ephemeral port against a hosted client_id URL; the same-origin
            # rule cannot apply and does not need to (#2846 handles the port).
            continue
        if require_same_origin and not _same_origin(parsed, client_url):
            raise CimdError(
                "non-loopback redirect_uris must be same-origin with the "
                "client_id URL.")

    grants = document.get("grant_types")
    if grants is None:
        grants = ["authorization_code", "refresh_token"]
    if (not isinstance(grants, list) or not grants
            or not set(grants).issubset(supported_grants)):
        raise CimdError("client metadata document declares an unsupported grant_types.")

    scope = document.get("scope")
    if scope is None:
        scope = default_scope
    if not isinstance(scope, str) or any(
            s not in supported_scopes for s in scope.split()):
        raise CimdError("client metadata document declares an unsupported scope.")

    return {
        "client_id": client_id,
        # Anti-phishing: the HOST is what the consent screen shows. The
        # document's own client_name is self-asserted and deliberately dropped.
        "client_name": client_url.hostname or client_id,
        "redirect_uris": list(redirect_uris),
        "grant_types": list(grants),
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": scope,
    }


def resolve_client_metadata(client_id: str, *, supported_scopes: set[str],
                            supported_grants: set[str],
                            default_scope: str) -> dict:
    """Full CIMD resolution: validate → cache → bounded fetch → validate.

    A cached hit skips EVERY bound (the rate limit, the in-flight cap and the
    wall-clock budget all bound *fetches*, not resolutions). Nothing that
    raised is ever cached, so a transient failure is retried on the next
    request instead of being pinned for the TTL.

    #3669 — the fetch is bounded three ways, all charged HERE so that the four
    unauthenticated front doors (`/oauth/authorize`, `/oauth/consent`, and the
    auth-code + refresh grants of `/oauth/token`) are counted identically:
    fetch COUNT (the rate limiter above), CONCURRENT fetches
    (``MAX_IN_FLIGHT_FETCHES``) and total wall-clock SECONDS per window
    (``FETCH_BUDGET_S``). The last two exist because count alone does not bound
    the product, and this function is the one place every door passes through.
    """
    client_id = validate_client_id_url(client_id)
    cached = _cache_get(client_id)
    if cached is not None:
        return cached
    # Bind the semaphore LOCALLY: a concurrent `_rate_limit_reset` (test/ops
    # seam) rebinds the module global, and releasing the global in the `finally`
    # would release the NEW semaphore — raising "released too many times" out of
    # the finally and masking the real result, while leaking a permit here.
    sem = _IN_FLIGHT
    if not sem.acquire(timeout=IN_FLIGHT_WAIT_S):
        raise CimdError("CIMD fetch concurrency limit reached.")
    try:
        reserved_window = _budget_reserve()
        started = time.monotonic()
        try:
            # The rate-limit charge and the fetch share ONE settled region: a
            # refusal from the limiter must not leave the reservation charged
            # (that leaked 8 s per refused request and drained the window).
            _charge_rate_limit(urlparse(client_id).hostname or "")
            document = fetch_client_metadata(client_id)
        finally:
            _budget_settle(time.monotonic() - started, reserved_window)
        record = validate_document(
            client_id, document,
            supported_scopes=supported_scopes,
            supported_grants=supported_grants,
            default_scope=default_scope)
        _cache_put(client_id, record)
        return record
    finally:
        sem.release()
