"""Client-IP middleware tests (#1081 Task 0).

POST /v1/agent/signup (and the other per-IP limiters: /v1/register 3/hr,
sensitive ops) must key on the REAL client IP, not the Fly proxy's IP.
Fly Proxy sets ``Fly-Client-IP`` from the connection peer (non-spoofable —
the proxy overwrites any client-supplied value; X-Forwarded-For is
documented "treat with caution" and uvicorn only trusts it from 127.0.0.1).
The middleware resolves ``request.state.client_ip`` for every request;
limiters read it with a ``request.client.host`` fallback.
"""
import pytest
from starlette.datastructures import State

from tortoise.hosted_api import ClientIPMiddleware, ForwardedProtoMiddleware


class _FakeRequest:
    """Request stand-in with headers/client/scope-backed state.

    Mirrors starlette's Request.state (a State over the shared
    ``scope["state"]`` dict) — the surface ClientIPMiddleware.dispatch
    touches. ``scope["scheme"]`` carries the redirect-Location scheme
    that ForwardedProtoMiddleware rewrites (#985).
    """

    def __init__(self, headers=None, client_host="203.0.113.5", scheme="http"):
        self.headers = headers or {}
        self.client = (type("Client", (), {"host": client_host})()
                       if client_host is not None else None)
        self.scope = {"state": {}, "scheme": scheme}
        self.state = State(self.scope["state"])


def _middleware():
    return ClientIPMiddleware(lambda scope, receive, send: None)


@pytest.mark.asyncio
async def test_fly_client_ip_header_populates_state(monkeypatch):
    # #1081 review P2-2: the header is trusted ONLY when
    # TORTOISE_TRUST_FLY_CLIENT_IP=1 (hosted Fly image) — fail-closed
    # otherwise so a non-proxy ingress can never set its own IP.
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = _FakeRequest(headers={"Fly-Client-IP": "203.0.113.7"})

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "203.0.113.7"


@pytest.mark.asyncio
async def test_fly_client_ip_header_ignored_without_trust_flag(monkeypatch):
    # Fail-closed: without the trust flag, a client-supplied Fly-Client-IP
    # must NOT override the TCP peer (limiters key on client.host) —
    # otherwise local dev / selfhost / non-proxy ingress could reset every
    # per-IP limiter key (review P2-2).
    monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
    req = _FakeRequest(headers={"Fly-Client-IP": "203.0.113.7"},
                       client_host="198.51.100.9")

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "198.51.100.9"  # peer, not header


@pytest.mark.asyncio
async def test_falls_back_to_client_host_when_header_absent():
    req = _FakeRequest(headers={}, client_host="198.51.100.9")

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "198.51.100.9"


@pytest.mark.asyncio
async def test_forged_x_forwarded_for_ignored(monkeypatch):
    """A client-forged XFF must NOT change the resolved IP (non-spoofable:
    the middleware only reads Fly-Client-IP, which the proxy sets/overwrites;
    XFF is never consulted).
    """
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = _FakeRequest(headers={"Fly-Client-IP": "203.0.113.7",
                                "X-Forwarded-For": "6.6.6.6"})

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "203.0.113.7"

    # and with NO Fly-Client-IP, XFF still cannot hijack the fallback
    req2 = _FakeRequest(headers={"X-Forwarded-For": "6.6.6.6"},
                        client_host="198.51.100.9")
    await _middleware().dispatch(req2, call_next)
    assert req2.state.client_ip == "198.51.100.9"


@pytest.mark.asyncio
async def test_no_client_no_header_resolves_none():
    """Neither header nor client → client_ip None (limiters fall back to
    client.host, which is also None → no bucket key → request passes)."""
    req = _FakeRequest(headers={}, client_host=None)

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip is None


# ═══════════════════════════════════════════════════════════════════════
# ForwardedProtoMiddleware (#985)
# ═══════════════════════════════════════════════════════════════════════


def _proto_middleware():
    return ForwardedProtoMiddleware(lambda scope, receive, send: None)


async def _dispatch_proto(mw, req):
    async def call_next(request):
        return request

    await mw.dispatch(req, call_next)
    return req


@pytest.mark.asyncio
async def test_xfp_https_rewrites_scope_scheme(monkeypatch):
    """Behind the trusted Fly proxy (flag=1), X-Forwarded-Proto: https must
    rewrite scope["scheme"] so redirect Locations stay https (#985)."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "https"}),
    )
    assert req.scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_xfp_first_value_wins(monkeypatch):
    """Proxy chains append values ("https,http") — the first is the
    client-facing scheme (RFC 7239 ordering)."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "https, http"}),
    )
    assert req.scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_xfp_ignored_without_trust_flag(monkeypatch):
    """Fail-closed: without ANY trust flag, a client-forged X-Forwarded-Proto
    must NOT change the scheme — self-host / LAN / direct-port ingress could
    otherwise spoof https in its own redirects (review P2-2 pattern, #1081)."""
    monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
    monkeypatch.delenv("TORTOISE_TRUST_X_FORWARDED_PROTO", raising=False)
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "https"}, scheme="http"),
    )
    assert req.scope["scheme"] == "http"  # unchanged


@pytest.mark.asyncio
async def test_xfp_invalid_value_ignored(monkeypatch):
    """Non-http(s) values (e.g. "ftp") are rejected — never write garbage
    into scope."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "ftp"}, scheme="http"),
    )
    assert req.scope["scheme"] == "http"


@pytest.mark.asyncio
async def test_xfp_absent_is_noop(monkeypatch):
    """No header → scheme untouched (direct http ingress keeps http)."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={}, scheme="http"),
    )
    assert req.scope["scheme"] == "http"


@pytest.mark.asyncio
async def test_fly_forwarded_proto_rewrites_scope_scheme(monkeypatch):
    """Behind the trusted Fly proxy (flag=1), the proxy-set
    Fly-Forwarded-Proto must rewrite scope["scheme"] (#985)."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"Fly-Forwarded-Proto": "https"}),
    )
    assert req.scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_fly_forwarded_proto_beats_client_xfp(monkeypatch):
    """Review P2: X-Forwarded-Proto is client-overridable behind Fly (the
    proxy passes it through unchanged), while Fly-Forwarded-Proto is proxy-
    set and non-spoofable. In the trusted path, a client-supplied
    X-Forwarded-Proto (here attempting a downgrade to http) must NEVER win
    over Fly-Forwarded-Proto — the redirect stays https."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={
            "Fly-Forwarded-Proto": "https",
            "X-Forwarded-Proto": "http",
        }),
    )
    assert req.scope["scheme"] == "https"  # Fly-Forwarded-Proto wins


@pytest.mark.asyncio
async def test_xfp_fallback_when_fly_forwarded_proto_absent(monkeypatch):
    """Trusted Fly path without a Fly-Forwarded-Proto header (e.g. health
    checks from inside the Fly network) falls back to X-Forwarded-Proto
    exactly as before #985's P2 split."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "https"}),
    )
    assert req.scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_xfp_trusted_with_selfhost_flag(monkeypatch):
    """Review P2: TORTOISE_TRUST_X_FORWARDED_PROTO=1 alone (self-hoster
    behind nginx/Caddy — no Fly edge) trusts X-Forwarded-Proto exactly as
    that proxy set it, without enabling any Fly-Client-IP trust."""
    monkeypatch.delenv("TORTOISE_TRUST_FLY_CLIENT_IP", raising=False)
    monkeypatch.setenv("TORTOISE_TRUST_X_FORWARDED_PROTO", "1")
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={"X-Forwarded-Proto": "https"}),
    )
    assert req.scope["scheme"] == "https"


@pytest.mark.asyncio
async def test_fly_flag_alone_does_not_trust_fly_forwarded_proto_absent_xfp(
    monkeypatch,
):
    """With only the Fly flag, no forwarded-proto header at all → scheme
    unchanged (fail-closed default holds for the fly domain too)."""
    monkeypatch.setenv("TORTOISE_TRUST_FLY_CLIENT_IP", "1")
    monkeypatch.delenv("TORTOISE_TRUST_X_FORWARDED_PROTO", raising=False)
    req = await _dispatch_proto(
        _proto_middleware(),
        _FakeRequest(headers={}, scheme="http"),
    )
    assert req.scope["scheme"] == "http"
