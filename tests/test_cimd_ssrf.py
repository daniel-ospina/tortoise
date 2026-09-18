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
"""
from __future__ import annotations

import ipaddress
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


# ── Feature gate ───────────────────────────────────────────────────────────

def test_cimd_enabled_by_default(monkeypatch):
    monkeypatch.delenv("TORTOISE_OAUTH_CIMD", raising=False)
    assert cimd.cimd_enabled()
    assert cimd.client_id_metadata_document_supported()


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no", "off", ""])
def test_cimd_can_be_disabled(monkeypatch, value):
    monkeypatch.setenv("TORTOISE_OAUTH_CIMD", value)
    assert not cimd.cimd_enabled()
