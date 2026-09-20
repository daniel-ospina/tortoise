"""CIMD SSRF control set + document semantics (#2847).

Every control the issue enumerates has a test here, plus the Anthropic
connector-doc rules that are not SSRF controls but are part of the mechanism
(self-referential document, public-client-only, consent screen shows the HOST).
The end-to-end "obtain a client identity without POST /register" assertion lives
in ``tests/test_oauth_mcp.py`` (it needs the control-plane harness).

Controls under test
-------------------
1. ``validate_client_id_url`` — scheme/host/path/userinfo/fragment/dot-segments
2. ``public_addresses`` + ``is_public_address`` + ``_PinningNetworkBackend``
   (the DNS-rebinding-safe pin)
3. redirect refusal
4. size + status handling
5. fetch cache (successes only; TTL; LRU cap; NEVER negative)
6. fetch rate limiting (per-host, aggregate, store cap)
7. total occupancy (#3669): in-flight cap, absolute fetch deadline, per-window
   wall-clock budget reserved at admission
"""
from __future__ import annotations

import ipaddress
import threading
import time
from typing import ClassVar

import httpcore
import pytest

from tortoise import cimd

CLIENT_ID = "https://claude.ai/.well-known/oauth-client-metadata"


@pytest.fixture(autouse=True)
def _clean_cimd_state(monkeypatch):
    """The rate-limit and cache stores are module state (like
    ``_OAUTH_DCR_BUCKETS``) — reset per test so ordering cannot matter."""
    monkeypatch.delenv("TORTOISE_OAUTH_CIMD", raising=False)
    monkeypatch.delenv("TORTOISE_OAUTH_CIMD_SAME_ORIGIN", raising=False)
    cimd._rate_limit_reset()
    cimd._cache_reset()
    yield
    cimd._rate_limit_reset()
    cimd._cache_reset()


# ── Control 1 — client_id URL validation ───────────────────────────────────

@pytest.mark.parametrize("url", [
    CLIENT_ID,
    "https://example.com/oauth/client.json",
    "https://example.com:8443/client",
    "https://example.com/client?tenant=1",
    "https://xn--bcher-kva.example/client",
])
def test_client_id_urls_accepted(url):
    assert cimd.validate_client_id_url(url) == url, (
        "returned verbatim: §4.1 requires simple string comparison against the "
        "document's own client_id")


@pytest.mark.parametrize("url,reason", [
    ("http://example.com/client", "scheme"),
    ("ftp://example.com/client", "scheme"),
    ("file:///etc/passwd", "scheme"),
    ("https://example.com", "path"),
    ("https://example.com/", "path"),
    ("https://u:p@example.com/client", "userinfo"),
    ("https://user@example.com/client", "userinfo"),
    ("https://example.com/client#frag", "fragment"),
    ("https://example.com/a/../b", "dot-segment"),
    ("https://example.com/./b", "dot-segment"),
    ("https://example.com/a/%2e%2e/b", "encoded dot-segment"),
    ("https://example.com/a/%2e%2e%2fb", "encoded separator + dot-segment"),
    ("https://example.com/%252e%252e%252fetc", "double-encoded dot-segment"),
    ("https://example.com/%2e%2e%2f%2e%2e%2fetc/passwd", "encoded traversal"),
    # #2847 review P2: the consent screen shows the HOST, so a non-ASCII (IDN)
    # host is a homograph (`сlaude.ai` with a Cyrillic с). Punycode only.
    ("https://ex\u00e4mple.com/client", "non-ASCII host"),
    ("https://\u0441laude.ai/.well-known/oauth-client-metadata", "IDN homograph"),
    ("https:///client", "host"),
    ("https://example.com/cl ient", "whitespace"),
    ("https://example.com/cl\x00ient", "control char"),
    ("https://example.com/" + "a" * cimd.CLIENT_ID_MAX_LEN, "length"),
    ("", "empty"),
])
def test_client_id_urls_refused(url, reason):
    with pytest.raises(cimd.CimdError):
        cimd.validate_client_id_url(url)


@pytest.mark.parametrize("value", [None, 42, b"https://x/y", ["https://x/y"]])
def test_client_id_url_rejects_non_strings(value):
    with pytest.raises(cimd.CimdError):
        cimd.validate_client_id_url(value)


def test_is_cimd_client_id_only_matches_https_urls():
    assert cimd.is_cimd_client_id(CLIENT_ID)
    assert not cimd.is_cimd_client_id("ct_abc123")     # a DCR client_id
    assert not cimd.is_cimd_client_id("http://x/y")    # never fetched
    assert not cimd.is_cimd_client_id(None)


# ── Control 2 — host validation ────────────────────────────────────────────

@pytest.mark.parametrize("addr", [
    "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.0.1", "192.168.1.1",
    "169.254.169.254",     # cloud metadata
    "100.64.0.1",          # carrier-grade NAT
    "0.0.0.0",             # unspecified
    "224.0.0.1",           # multicast — and is_global is True for this!
    "240.0.0.1",           # reserved
    "192.0.0.1",           # IETF protocol assignments
    "198.18.0.1",          # benchmarking
    "::1", "fc00::1", "fe80::1",
    "::ffff:127.0.0.1",    # IPv4-mapped loopback
    "64:ff9b::1.2.3.4",    # NAT64-embedded — is_global True, reserved
    # #2847 review P2 — deprecated special-purpose ranges that `is_global`
    # still reports as global.
    "fec0::1",             # IPv6 site-local, RFC 3879
    "192.88.99.1",         # 6to4 relay anycast, RFC 7526
])
def test_non_public_addresses_refused(addr):
    assert not cimd.is_public_address(ipaddress.ip_address(addr))


@pytest.mark.parametrize("addr", ["8.8.8.8", "1.1.1.1", "160.79.104.11",
                                  "2606:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses_accepted(addr):
    assert cimd.is_public_address(ipaddress.ip_address(addr))


def _resolver(*addresses):
    return lambda *a, **k: [
        (2, 1, 6, "", (addr, 443)) for addr in addresses]


def test_public_addresses_refuses_private(monkeypatch):
    monkeypatch.setattr(cimd.socket, "getaddrinfo", _resolver("127.0.0.1"))
    with pytest.raises(cimd.CimdError, match="non-public"):
        cimd.public_addresses("evil.example", 443)


def test_public_addresses_refuses_mixed_public_and_private(monkeypatch):
    """Publishing one public A record beside a private one must not help: the
    whole resolution is refused, not filtered."""
    monkeypatch.setattr(cimd.socket, "getaddrinfo",
                        _resolver("93.184.216.34", "10.0.0.1"))
    with pytest.raises(cimd.CimdError, match="non-public"):
        cimd.public_addresses("evil.example", 443)


def test_public_addresses_refuses_unresolvable(monkeypatch):
    def _boom(*a, **k):
        raise OSError("no such host")
    monkeypatch.setattr(cimd.socket, "getaddrinfo", _boom)
    with pytest.raises(cimd.CimdError, match="could not resolve"):
        cimd.public_addresses("nope.example", 443)


class _RecordingBackend:
    """Stands in for ``httpcore.SyncBackend`` and records the connect target."""

    def __init__(self):
        self.targets = []

    def connect_tcp(self, host, port, timeout=None, local_address=None,
                    socket_options=None):
        self.targets.append((host, port))
        if host.startswith("93."):
            return "stream"
        raise OSError("refused")


def test_pinning_backend_connects_to_the_vetted_address(monkeypatch):
    """The socket destination is the validated IP, never re-resolved from the
    name — this is the control that closes the DNS-rebinding TOCTOU."""
    monkeypatch.setattr(cimd.socket, "getaddrinfo",
                        _resolver("93.184.216.34"))
    inner = _RecordingBackend()
    backend = cimd._PinningNetworkBackend(inner=inner)
    assert backend.connect_tcp("claude.ai", 443) == "stream"
    assert inner.targets == [("93.184.216.34", 443)]


def test_pinning_backend_never_connects_to_a_private_target(monkeypatch):
    monkeypatch.setattr(cimd.socket, "getaddrinfo", _resolver("10.1.2.3"))
    inner = _RecordingBackend()
    backend = cimd._PinningNetworkBackend(inner=inner)
    with pytest.raises(cimd.CimdError):
        backend.connect_tcp("evil.example", 443)
    assert inner.targets == [], "no socket may be opened before validation"


def test_pinning_backend_tries_each_vetted_address(monkeypatch):
    monkeypatch.setattr(cimd.socket, "getaddrinfo",
                        _resolver("93.184.216.34", "93.184.216.35"))
    inner = _RecordingBackend()
    backend = cimd._PinningNetworkBackend(inner=inner)
    assert backend.connect_tcp("claude.ai", 443) == "stream"
    assert inner.targets[0] == ("93.184.216.34", 443)


def test_pinning_backend_refuses_unix_sockets():
    backend = cimd._PinningNetworkBackend(inner=_RecordingBackend())
    with pytest.raises(httpcore.ConnectError, match="unix sockets"):
        backend.connect_unix_socket("/var/run/docker.sock")


# ── Controls 3 + 4 — redirects, size, status ───────────────────────────────

class _FakeResponse:
    def __init__(self, status, chunks=(), headers=()):
        self.status = status
        self.headers = list(headers)
        self._chunks = chunks

    def iter_stream(self):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePool:
    """Scripted stand-in for ``httpcore.ConnectionPool``; records construction
    kwargs so the wiring (pinning backend present) is assertable."""

    instances: ClassVar[list[_FakePool]] = []
    script: ClassVar[list] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.script = list(_FakePool.script)
        _FakePool.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        self.request = (method, url, kwargs)
        return self.script.pop(0)


@pytest.fixture
def fake_pool(monkeypatch):
    _FakePool.instances = []
    _FakePool.script = []
    monkeypatch.setattr(cimd.httpcore, "ConnectionPool", _FakePool)
    return _FakePool


def test_fetch_returns_parsed_document(fake_pool):
    fake_pool.script = [_FakeResponse(200, [b'{"client_id": "', b'x"}'])]
    assert cimd.fetch_client_metadata(CLIENT_ID) == {"client_id": "x"}


def test_fetch_uses_the_pinning_backend(fake_pool):
    fake_pool.script = [_FakeResponse(200, [b"{}"])]
    cimd.fetch_client_metadata(CLIENT_ID)
    backend = fake_pool.instances[-1].kwargs["network_backend"]
    assert isinstance(backend, cimd._PinningNetworkBackend), (
        "the fetch must run on the pinning backend or control 2 is bypassed")


def test_fetch_refuses_redirects(fake_pool):
    fake_pool.script = [_FakeResponse(302, [], [("location", "http://169.254.169.254/")])]
    with pytest.raises(cimd.CimdError, match="redirect"):
        cimd.fetch_client_metadata(CLIENT_ID)


@pytest.mark.parametrize("status", [301, 303, 307, 308, 400, 401, 403, 404, 500])
def test_fetch_refuses_every_non_200(fake_pool, status):
    fake_pool.script = [_FakeResponse(status, [b"{}"])]
    with pytest.raises(cimd.CimdError):
        cimd.fetch_client_metadata(CLIENT_ID)


def test_fetch_enforces_the_size_cap(fake_pool):
    oversize = b"x" * (cimd.DOCUMENT_MAX_BYTES + 1)
    fake_pool.script = [_FakeResponse(200, [oversize])]
    with pytest.raises(cimd.CimdError, match="size cap"):
        cimd.fetch_client_metadata(CLIENT_ID)


def test_fetch_uses_a_timeout_extension(fake_pool):
    fake_pool.script = [_FakeResponse(200, [b"{}"])]
    cimd.fetch_client_metadata(CLIENT_ID)
    timeout = fake_pool.instances[-1].request[2]["extensions"]["timeout"]
    assert timeout["connect"] == cimd.CONNECT_TIMEOUT_S
    assert timeout["read"] == cimd.READ_TIMEOUT_S


@pytest.mark.parametrize("body", [b"not json", b"[1,2,3]", b"\xff\xfe", b'"str"'])
def test_fetch_refuses_non_object_json(fake_pool, body):
    fake_pool.script = [_FakeResponse(200, [body])]
    with pytest.raises(cimd.CimdError):
        cimd.fetch_client_metadata(CLIENT_ID)


def test_fetch_wraps_transport_errors(fake_pool):
    fake_pool.script = []
    with pytest.raises(cimd.CimdError):
        cimd.fetch_client_metadata(CLIENT_ID)


# ── Document semantics ─────────────────────────────────────────────────────

def _doc(**overrides):
    doc = {
        "client_id": CLIENT_ID,
        "client_name": "Totally Not Claude",
        "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
    }
    doc.update(overrides)
    return doc


def _validate(doc, **kw):
    kw.setdefault("supported_scopes", {"mcp", "offline_access"})
    kw.setdefault("supported_grants", {"authorization_code", "refresh_token"})
    kw.setdefault("default_scope", "mcp")
    return cimd.validate_document(CLIENT_ID, doc, **kw)


def test_valid_document_normalizes_to_a_public_client():
    record = _validate(_doc())
    assert record["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in record
    assert record["response_types"] == ["code"]
    assert record["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]


def test_display_name_is_the_host_not_the_self_asserted_name():
    """Anthropic's consent-screen rule: a client must not name itself. The
    document says 'Totally Not Claude'; we show claude.ai."""
    assert _validate(_doc())["client_name"] == "claude.ai"


def test_document_must_be_self_referential():
    with pytest.raises(cimd.CimdError, match="self-referential"):
        _validate(_doc(client_id="https://evil.example/other"))
    with pytest.raises(cimd.CimdError, match="self-referential"):
        _validate({"redirect_uris": ["https://claude.ai/cb"]})


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic",
                                    "private_key_jwt", "tls_client_auth"])
def test_confidential_auth_methods_refused(method):
    with pytest.raises(cimd.CimdError, match="token_endpoint_auth_method"):
        _validate(_doc(token_endpoint_auth_method=method))


def test_redirect_uris_are_required():
    for bad in (None, [], "https://claude.ai/cb", [""], [42]):
        with pytest.raises(cimd.CimdError):
            _validate(_doc(redirect_uris=bad))


def test_redirect_uri_must_be_https_or_loopback():
    with pytest.raises(cimd.CimdError, match="https"):
        _validate(_doc(redirect_uris=["http://evil.example/cb"]))


def test_non_loopback_redirect_must_be_same_origin():
    with pytest.raises(cimd.CimdError, match="same-origin"):
        _validate(_doc(redirect_uris=["https://attacker.example/cb"]))


def test_same_origin_rule_is_a_reversible_lever(monkeypatch):
    monkeypatch.setenv("TORTOISE_OAUTH_CIMD_SAME_ORIGIN", "0")
    assert _validate(_doc(redirect_uris=["https://other.example/cb"]))


def test_default_port_is_the_same_origin():
    record = _validate(_doc(redirect_uris=["https://claude.ai:443/cb"]))
    assert record["redirect_uris"] == ["https://claude.ai:443/cb"]


def test_loopback_redirect_is_exempt_from_same_origin():
    """Claude Code declares a loopback listener against a hosted client_id URL
    — the same-origin rule cannot apply and #2846 handles the port."""
    for uri in ("http://127.0.0.1/callback", "http://localhost:49152/callback",
                "http://[::1]:8080/callback"):
        assert _validate(_doc(redirect_uris=[uri]))["redirect_uris"] == [uri]


def test_unsupported_grant_types_refused():
    with pytest.raises(cimd.CimdError, match="grant_types"):
        _validate(_doc(grant_types=["client_credentials"]))
    with pytest.raises(cimd.CimdError, match="grant_types"):
        _validate(_doc(grant_types=[]))


def test_unsupported_scope_refused():
    with pytest.raises(cimd.CimdError, match="scope"):
        _validate(_doc(scope="admin"))


def test_defaults_applied_when_optional_fields_absent():
    record = _validate(_doc())
    assert record["grant_types"] == ["authorization_code", "refresh_token"]
    assert record["scope"] == "mcp"


# ── Control 5 — fetch cache ────────────────────────────────────────────────

def _resolve(monkeypatch, doc, fetches):
    def _fetch(client_id):
        fetches.append(client_id)
        return doc
    monkeypatch.setattr(cimd, "fetch_client_metadata", _fetch)
    return cimd.resolve_client_metadata(
        CLIENT_ID, supported_scopes={"mcp", "offline_access"},
        supported_grants={"authorization_code", "refresh_token"},
        default_scope="mcp")


def _resolve_direct():
    """Resolution with NO fetch patched here — for tests that patch
    ``fetch_client_metadata`` themselves (``_resolve`` would overwrite it)."""
    return cimd.resolve_client_metadata(
        CLIENT_ID, supported_scopes={"mcp", "offline_access"},
        supported_grants={"authorization_code", "refresh_token"},
        default_scope="mcp")


def test_cache_serves_the_second_resolution_without_fetching(monkeypatch):
    fetches: list[str] = []
    _resolve(monkeypatch, _doc(), fetches)
    _resolve(monkeypatch, _doc(), fetches)
    assert fetches == [CLIENT_ID]


def test_cache_expires_after_ttl(monkeypatch):
    fetches: list[str] = []
    _resolve(monkeypatch, _doc(), fetches)
    # Age the cached entry past the TTL rather than patching `time.monotonic`,
    # which is the stdlib module shared with everything else in the process.
    stored_at, document = cimd._CACHE[CLIENT_ID]
    cimd._CACHE[CLIENT_ID] = (stored_at - cimd.CACHE_TTL_S - 1, document)
    _resolve(monkeypatch, _doc(), fetches)
    assert len(fetches) == 2


def test_errors_are_never_cached(monkeypatch):
    """Spec §4.3: MUST NOT cache error responses. A refused fetch is retried."""
    calls: list[str] = []

    def _flaky(client_id):
        calls.append(client_id)
        if len(calls) == 1:
            raise cimd.CimdError("boom")
        return _doc()

    monkeypatch.setattr(cimd, "fetch_client_metadata", _flaky)
    with pytest.raises(cimd.CimdError):
        _resolve_direct()
    assert cimd._CACHE == {}
    _resolve_direct()
    assert len(calls) == 2, "the failure must not be pinned for the TTL"


def test_malformed_documents_are_never_cached(monkeypatch):
    def _bad(client_id):
        return _doc(client_id="https://evil.example/mismatch")
    monkeypatch.setattr(cimd, "fetch_client_metadata", _bad)
    with pytest.raises(cimd.CimdError):
        _resolve_direct()
    assert cimd._CACHE == {}


def test_cache_is_lru_bounded(monkeypatch):
    monkeypatch.setattr(cimd, "CACHE_CAP", 2)
    for n in range(3):
        cimd._cache_put(f"https://h{n}.example/c", {"n": n})
    assert len(cimd._CACHE) == 2
    assert "https://h0.example/c" not in cimd._CACHE


# ── Control 6 — fetch rate limiting ────────────────────────────────────────

def test_per_host_rate_limit(monkeypatch):
    monkeypatch.setattr(cimd, "PER_HOST_PER_HOUR", 3)
    for _ in range(3):
        cimd._charge_rate_limit("claude.ai")
    with pytest.raises(cimd.CimdError, match="rate limit"):
        cimd._charge_rate_limit("claude.ai")


def test_rate_limit_is_per_host(monkeypatch):
    monkeypatch.setattr(cimd, "PER_HOST_PER_HOUR", 1)
    cimd._charge_rate_limit("a.example")
    cimd._charge_rate_limit("b.example")     # unaffected by a.example's bucket


def test_aggregate_rate_limit(monkeypatch):
    monkeypatch.setattr(cimd, "AGGREGATE_PER_HOUR", 2)
    cimd._charge_rate_limit("a.example")
    cimd._charge_rate_limit("b.example")
    with pytest.raises(cimd.CimdError, match="aggregate"):
        cimd._charge_rate_limit("c.example")


def test_rate_limit_store_is_bounded(monkeypatch):
    monkeypatch.setattr(cimd, "STORE_CAP", 2)
    monkeypatch.setattr(cimd, "PER_HOST_PER_HOUR", 1)
    for n in range(5):
        cimd._charge_rate_limit(f"h{n}.example")
    assert len(cimd._RATE_BUCKETS) <= 2


def test_cache_hits_do_not_consume_the_fetch_budget(monkeypatch):
    fetches: list[str] = []
    resolved = _resolve(monkeypatch, _doc(), fetches)
    assert resolved["client_id"] == CLIENT_ID
    charged = len(cimd._RATE_AGGREGATE)
    _resolve(monkeypatch, _doc(), fetches)      # cache hit
    assert len(cimd._RATE_AGGREGATE) == charged, (
        "the limit bounds fetches, not resolutions")


def test_rate_limited_fetch_raises_without_caching(monkeypatch):
    monkeypatch.setattr(cimd, "PER_HOST_PER_HOUR", 0)
    monkeypatch.setattr(cimd, "fetch_client_metadata",
                        lambda _c: pytest.fail("must not fetch"))
    with pytest.raises(cimd.CimdError, match="rate limit"):
        _resolve_direct()
    assert cimd._CACHE == {}


# ── Control 7 — total occupancy (in-flight cap + wall-clock budget, #3669) ──
#
# The rate limiter bounds fetch COUNT. These bound the PRODUCT (count x
# duration): concurrent fetches, and total seconds per window. Both are charged
# in ``resolve_client_metadata`` — the one function all four unauthenticated
# front doors reach through ``resolve_client``.

def test_fetch_budget_reserves_the_worst_case_and_refuses_a_later_fetch(monkeypatch):
    """A fetch RESERVES FETCH_MAX_S up front, settles to actual on return, and
    once the window cannot fit another reservation the next fetch is refused
    BEFORE it starts (and before it charges the rate limiter)."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 0.5)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 1.0)
    monkeypatch.setattr(cimd, "CACHE_TTL_S", 0)   # force a second fetch
    calls: list[str] = []

    def _slow(client_id):
        calls.append(client_id)
        time.sleep(0.6)
        return _doc()

    monkeypatch.setattr(cimd, "fetch_client_metadata", _slow)
    _resolve_direct()                               # settles to ~0.6 actual
    assert cimd._BUDGET_SPENT > 0.0
    with pytest.raises(cimd.CimdError, match="wall-clock budget"):
        _resolve_direct()
    assert len(calls) == 1, "the refused fetch must not have been attempted"


def test_budget_reservation_is_refunded_to_actual(monkeypatch):
    """The FETCH_MAX_S reservation is refunded to the real duration on return,
    so a fast fetch does not permanently consume its worst case."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 5.0)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 100.0)
    monkeypatch.setattr(cimd, "fetch_client_metadata", lambda _c: _doc())
    _resolve_direct()
    assert cimd._BUDGET_SPENT < 1.0, (
        "the 5s worst-case reservation must be refunded to the ~0s actual")


def test_budget_reservation_is_refunded_when_the_rate_limiter_refuses(monkeypatch):
    """REGRESSION (cycle-2 review): the rate-limit charge sits INSIDE the
    settled region, so a refusal there must not leave the reservation charged
    for the rest of the window (that leaked the budget to cheap non-fetching
    requests and starved legitimate clients)."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 5.0)
    monkeypatch.setattr(cimd, "PER_HOST_PER_HOUR", 0)
    with pytest.raises(cimd.CimdError, match="rate limit"):
        _resolve_direct()
    assert cimd._BUDGET_SPENT < cimd.FETCH_MAX_S, (
        "a rate-refused request leaked its whole reservation")


def test_an_unsettled_reservation_stays_charged(monkeypatch):
    """An ABANDONED fetch (its caller's offload bound expired while the worker
    kept running) never reaches ``_budget_settle``, so its reservation stays
    charged — that is what keeps the budget a worst-case upper bound. There is
    no settle here on purpose."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 5.0)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 6.0)
    cimd._budget_reserve()          # abandoned: never settles
    assert cimd._BUDGET_SPENT == 5.0
    with pytest.raises(cimd.CimdError, match="wall-clock budget"):
        # Only 1s of the window remains — no second reservation can fit.
        cimd._budget_reserve()


def test_settle_cannot_reopen_a_rolled_over_window(monkeypatch):
    """A settle from a fetch that spanned a window boundary must not subtract a
    reservation the new window already reset (a negative ``_BUDGET_SPENT``
    would REOPEN budget the window is meant to have closed)."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 5.0)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 100.0)
    window = cimd._budget_reserve()
    with cimd._BUDGET_LOCK:          # simulate the roll-over resetting spent
        cimd._BUDGET_STARTED += cimd.RATE_WINDOW_S + 1
        cimd._BUDGET_SPENT = 0.0
    cimd._budget_settle(0.1, window)
    assert cimd._BUDGET_SPENT >= 0.0


def test_old_window_settle_does_not_erase_a_live_reservation(monkeypatch):
    """A cross-window settle must charge its ACTUAL elapsed, never refund a
    reservation the new window already reset — subtracting would credit the
    window up to a full reservation and cancel a fetch admitted into it."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 5.0)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 100.0)
    old_window = cimd._budget_reserve()          # W1 reservation
    with cimd._BUDGET_LOCK:                      # roll W1 -> W2
        cimd._BUDGET_STARTED += cimd.RATE_WINDOW_S + 1
        cimd._BUDGET_SPENT = 0.0
    cimd._budget_reserve()                       # a LIVE W2 reservation
    assert cimd._BUDGET_SPENT == 5.0
    cimd._budget_settle(0.1, old_window)         # W1's fetch settles late
    assert pytest.approx(5.1) == cimd._BUDGET_SPENT, (
        "the old window's settle must ADD its elapsed, not refund the live "
        "reservation")


def test_budget_refusal_does_not_charge_the_rate_limiter(monkeypatch):
    """A budget-refused request must not consume fetch-count budget: the
    budget is the OUTER bound and refusing inside it should not make the
    caller also pay the inner one."""
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", -1.0)
    monkeypatch.setattr(cimd, "fetch_client_metadata",
                        lambda _c: pytest.fail("must not fetch"))
    with pytest.raises(cimd.CimdError, match="wall-clock budget"):
        _resolve_direct()
    assert cimd._RATE_AGGREGATE == []


def test_budget_window_rolls_over(monkeypatch):
    """The budget is per RATE_WINDOW_S: once the window elapses the next
    check starts a fresh one (lazy roll-over, no background timer)."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 0.5)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", 1.0)
    monkeypatch.setattr(cimd, "CACHE_TTL_S", 0)
    calls: list[str] = []

    def _slow_once(client_id):
        calls.append(client_id)
        if len(calls) == 1:
            time.sleep(0.6)      # spend the whole first window
        return _doc()

    monkeypatch.setattr(cimd, "fetch_client_metadata", _slow_once)
    _resolve_direct()                              # spends the budget
    with pytest.raises(cimd.CimdError, match="wall-clock budget"):
        _resolve_direct()
    # Age the window past RATE_WINDOW_S by moving its start backwards.
    with cimd._BUDGET_LOCK:
        cimd._BUDGET_STARTED -= cimd.RATE_WINDOW_S + 1
    _resolve_direct()
    assert len(calls) == 2, "a new window must admit the fetch again"
    assert cimd._BUDGET_SPENT < cimd.FETCH_BUDGET_S


def test_in_flight_cap_refuses_when_all_slots_are_held(monkeypatch):
    """A saturated in-flight cap refuses after IN_FLIGHT_WAIT_S instead of
    parking the caller (and, on the offload pool, a worker) forever."""
    monkeypatch.setattr(cimd, "_IN_FLIGHT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cimd, "IN_FLIGHT_WAIT_S", 0.05)
    monkeypatch.setattr(cimd, "fetch_client_metadata",
                        lambda _c: pytest.fail("must not fetch"))
    cimd._IN_FLIGHT.acquire()
    try:
        with pytest.raises(cimd.CimdError, match="concurrency limit"):
            _resolve_direct()
    finally:
        cimd._IN_FLIGHT.release()


def test_in_flight_slot_is_released_on_success(monkeypatch):
    monkeypatch.setattr(cimd, "MAX_IN_FLIGHT_FETCHES", 1)
    monkeypatch.setattr(cimd, "_IN_FLIGHT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cimd, "IN_FLIGHT_WAIT_S", 0.05)
    fetches: list[str] = []
    _resolve(monkeypatch, _doc(), fetches)
    # A second, distinct client must still get the (single) slot.
    def _fetch(client_id):
        fetches.append(client_id)
        return _doc(client_id=client_id)
    monkeypatch.setattr(cimd, "fetch_client_metadata", _fetch)
    other = "https://claude.ai/other-client-metadata"
    record = cimd.resolve_client_metadata(
        other, supported_scopes={"mcp", "offline_access"},
        supported_grants={"authorization_code", "refresh_token"},
        default_scope="mcp")
    assert record["client_id"] == other
    assert len(fetches) == 2, "the first fetch left its slot held"


def test_in_flight_slot_is_released_on_failure(monkeypatch):
    monkeypatch.setattr(cimd, "MAX_IN_FLIGHT_FETCHES", 1)
    monkeypatch.setattr(cimd, "_IN_FLIGHT", threading.BoundedSemaphore(1))
    monkeypatch.setattr(cimd, "IN_FLIGHT_WAIT_S", 0.05)
    monkeypatch.setattr(cimd, "fetch_client_metadata",
                        lambda _c: (_ for _ in ()).throw(cimd.CimdError("boom")))
    with pytest.raises(cimd.CimdError, match="boom"):
        _resolve_direct()
    # The slot must be back: a refusal here would be the concurrency limit.
    monkeypatch.setattr(cimd, "fetch_client_metadata", lambda _c: _doc())
    assert _resolve_direct()["client_id"] == CLIENT_ID


def test_release_targets_the_acquired_semaphore_across_a_reset(monkeypatch):
    """The release must target the semaphore that was ACQUIRED. A concurrent
    ``_rate_limit_reset`` rebinds the module global to a fresh semaphore;
    releasing the global would then release a fully-permitted ``BoundedSemaphore``
    and raise "released too many times" out of the ``finally``, masking the
    resolution's real result."""
    real_reset = cimd._rate_limit_reset

    def _reset_mid_fetch(_client_id):
        real_reset()          # rebinds cimd._IN_FLIGHT
        return _doc()

    monkeypatch.setattr(cimd, "fetch_client_metadata", _reset_mid_fetch)
    assert _resolve_direct()["client_id"] == CLIENT_ID


def test_cache_hit_skips_the_occupancy_bounds(monkeypatch):
    """The cap and the budget bound FETCHES; a cached resolution pays neither
    (same doctrine as the rate limiter)."""
    fetches: list[str] = []
    _resolve(monkeypatch, _doc(), fetches)
    monkeypatch.setattr(cimd, "FETCH_BUDGET_S", -1.0)   # budget now exhausted
    monkeypatch.setattr(cimd, "_IN_FLIGHT", threading.BoundedSemaphore(0))
    monkeypatch.setattr(cimd, "fetch_client_metadata",
                        lambda _c: pytest.fail("a cache hit must not fetch"))
    assert _resolve_direct()["client_id"] == CLIENT_ID


def test_rate_limit_reset_clears_the_occupancy_bounds(monkeypatch):
    """One test seam for the whole limiter family, so ordering cannot leak."""
    monkeypatch.setattr(cimd, "_BUDGET_SPENT", 999.0)
    monkeypatch.setattr(cimd, "_IN_FLIGHT", threading.BoundedSemaphore(1))
    cimd._IN_FLIGHT.acquire()
    cimd._rate_limit_reset()
    assert cimd._BUDGET_SPENT == 0.0
    assert cimd._RATE_BUCKETS == {} and cimd._RATE_AGGREGATE == []
    assert cimd._IN_FLIGHT.acquire(timeout=0.01), (
        "the reset must hand back a fresh, fully-permitted semaphore")


# ── Control 7 — the absolute fetch deadline (a per-op read timeout is not one) ──

def test_read_capped_enforces_the_deadline():
    """``READ_TIMEOUT_S`` is per socket read, so a trickling body resets it
    forever; ``_read_capped`` takes an absolute deadline and refuses past it."""
    def _trickle():
        yield b"{"
        yield b"}"

    with pytest.raises(cimd.CimdError, match="deadline"):
        cimd._read_capped(_trickle(), cimd.DOCUMENT_MAX_BYTES,
                          deadline=time.monotonic() - 1)


def test_fetch_aborts_a_trickled_body_at_the_hard_deadline(fake_pool, monkeypatch):
    """BEHAVIOURAL: a body that trickles past FETCH_MAX_S is aborted even
    though each read is inside READ_TIMEOUT_S."""
    monkeypatch.setattr(cimd, "FETCH_MAX_S", 0.05)
    monkeypatch.setattr(cimd, "READ_TIMEOUT_S", 30.0)   # would never fire

    def _trickle():
        yield b"{"
        time.sleep(0.2)          # server stalls mid-body, under the read timeout
        yield b"}"

    fake_pool.script = [_FakeResponse(200, _trickle())]
    with pytest.raises(cimd.CimdError, match="deadline"):
        cimd.fetch_client_metadata(CLIENT_ID)


def test_fetch_passes_its_deadline_to_the_pinning_backend(fake_pool):
    """The connect loop must see the deadline, or a multi-address blackhole
    burns CONNECT_TIMEOUT_S per address (cycle-2 security finding)."""
    fake_pool.script = [_FakeResponse(200, [b"{}"])]
    cimd.fetch_client_metadata(CLIENT_ID)
    backend = fake_pool.instances[-1].kwargs["network_backend"]
    assert backend._deadline is not None


def test_connect_loop_caps_each_attempt_by_the_remaining_deadline(monkeypatch):
    monkeypatch.setattr(cimd, "public_addresses", lambda h, p: ["1.1.1.1", "1.1.1.2"])
    seen: list[float | None] = []

    class _Inner:
        def connect_tcp(self, host, port, timeout=None, **kw):
            seen.append(timeout)
            raise OSError("connection refused")

    backend = cimd._PinningNetworkBackend(inner=_Inner())
    backend._deadline = time.monotonic() + 1.0
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("h", 443, timeout=cimd.CONNECT_TIMEOUT_S)
    assert seen and all(t is not None and t <= 1.0 for t in seen), (
        "each connect attempt must be capped by the remaining deadline")


def test_connect_loop_refuses_past_the_deadline(monkeypatch):
    monkeypatch.setattr(cimd, "public_addresses", lambda h, p: ["1.1.1.1"])

    class _Inner:
        def connect_tcp(self, *a, **k):
            raise AssertionError("must not attempt a connect past the deadline")

    backend = cimd._PinningNetworkBackend(inner=_Inner(),
                                          deadline=time.monotonic() - 1)
    with pytest.raises(httpcore.ConnectTimeout):
        backend.connect_tcp("h", 443, timeout=cimd.CONNECT_TIMEOUT_S)


class _StubStream:
    """Records the timeout of each op; ``start_tls`` hands back the same shape."""

    def __init__(self):
        self.reads: list = []
        self.writes: list = []
        self.tls: list = []
        self.tls_result = None

    def read(self, max_bytes, timeout=None):
        self.reads.append(timeout)
        return b""

    def write(self, buffer, timeout=None):
        self.writes.append(timeout)

    def close(self):
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.tls.append(timeout)
        # A DISTINCT object, like the real ``SyncStream.start_tls``: the re-wrap
        # must target this one, not the pre-TLS stream.
        self.tls_result = _StubStream()
        return self.tls_result

    def get_extra_info(self, info):
        return None


def test_pinning_backend_wraps_the_stream_in_the_deadline_proxy(monkeypatch):
    """The deadline must travel WITH the stream, or TLS/header reads (which
    happen after connect) stay on the per-read timeout (#3669 cycle-3)."""
    monkeypatch.setattr(cimd, "public_addresses", lambda h, p: ["1.1.1.1"])
    inner = _StubStream()
    backend = cimd._PinningNetworkBackend(
        inner=type("I", (), {"connect_tcp": lambda self, *a, **k: inner})(),
        deadline=time.monotonic() + 5)
    stream = backend.connect_tcp("h", 443, timeout=cimd.CONNECT_TIMEOUT_S)
    assert isinstance(stream, cimd._DeadlineStream)


def test_deadline_stream_caps_every_op_and_refuses_when_spent():
    stub = _StubStream()
    stream = cimd._DeadlineStream(stub, time.monotonic() + 1.0)
    stream.read(10, timeout=30.0)
    stream.write(b"x", timeout=30.0)
    stream.start_tls(object(), timeout=30.0)
    assert stub.reads and stub.reads[0] <= 1.0
    assert stub.writes and stub.writes[0] <= 1.0
    assert stub.tls and stub.tls[0] <= 1.0

    expired = cimd._DeadlineStream(_StubStream(), time.monotonic() - 1)
    with pytest.raises(httpcore.ReadTimeout):
        expired.read(10, timeout=30.0)
    with pytest.raises(httpcore.WriteTimeout):
        expired.write(b"x", timeout=30.0)
    with pytest.raises(httpcore.ConnectTimeout):
        expired.start_tls(object(), timeout=30.0)


def test_deadline_stream_rewraps_after_start_tls():
    stub = _StubStream()
    stream = cimd._DeadlineStream(stub, time.monotonic() + 5.0)
    wrapped = stream.start_tls(object(), timeout=cimd.CONNECT_TIMEOUT_S)
    assert isinstance(wrapped, cimd._DeadlineStream)
    assert wrapped._inner is stub.tls_result, (
        "the re-wrap must target the POST-TLS stream, not the pre-TLS one")
    assert wrapped._deadline == stream._deadline, (
        "the SAME absolute deadline must survive the TLS upgrade, or post-TLS "
        "reads get a renewed budget and can outlive the fetch deadline")
    wrapped.read(10, timeout=cimd.READ_TIMEOUT_S)
    assert stub.tls_result.reads, "a post-TLS read must reach the new stream"


# ── Feature gate ───────────────────────────────────────────────────────────

def test_cimd_enabled_by_default(monkeypatch):
    monkeypatch.delenv("TORTOISE_OAUTH_CIMD", raising=False)
    assert cimd.cimd_enabled()
    assert cimd.client_id_metadata_document_supported()


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off"])
def test_cimd_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("TORTOISE_OAUTH_CIMD", value)
    assert not cimd.cimd_enabled()


@pytest.mark.parametrize("name", ["TORTOISE_OAUTH_CIMD", "TORTOISE_OAUTH_CIMD_SAME_ORIGIN"])
@pytest.mark.parametrize("blank", ["", " "])
def test_cimd_blank_is_unset_not_a_statement(monkeypatch, name, blank):
    """#4097: a blank value (`""` or whitespace-only) is UNSET, not "off".

    The pre-#4097 `_env_flag` carried `""` in its falsy tuple, so a blank value — what an
    unset CI secret materialises as — flipped BOTH default-ON levers. For
    `TORTOISE_OAUTH_CIMD_SAME_ORIGIN` that direction RELAXED the same-origin SSRF guard;
    after #4097 blank falls back to the default (required). `0`/`false`/`no`/`off`
    remain explicit OFF.
    """
    monkeypatch.setenv(name, blank)
    resolver = (cimd.same_origin_redirects_required if name.endswith("SAME_ORIGIN")
                else cimd.cimd_enabled)
    assert resolver() is True
