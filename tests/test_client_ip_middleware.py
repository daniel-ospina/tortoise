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

from tortoise.hosted_api import ClientIPMiddleware


class _FakeRequest:
    """Request stand-in with headers/client/scope-backed state.

    Mirrors starlette's Request.state (a State over the shared
    ``scope["state"]`` dict) — the surface ClientIPMiddleware.dispatch
    touches.
    """

    def __init__(self, headers=None, client_host="203.0.113.5"):
        self.headers = headers or {}
        self.client = (type("Client", (), {"host": client_host})()
                       if client_host is not None else None)
        self.scope = {"state": {}}
        self.state = State(self.scope["state"])


def _middleware():
    return ClientIPMiddleware(lambda scope, receive, send: None)


@pytest.mark.asyncio
async def test_fly_client_ip_header_populates_state():
    req = _FakeRequest(headers={"Fly-Client-IP": "203.0.113.7"})

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "203.0.113.7"


@pytest.mark.asyncio
async def test_falls_back_to_client_host_when_header_absent():
    req = _FakeRequest(headers={}, client_host="198.51.100.9")

    async def call_next(request):
        return request

    await _middleware().dispatch(req, call_next)
    assert req.state.client_ip == "198.51.100.9"


@pytest.mark.asyncio
async def test_forged_x_forwarded_for_ignored():
    """A client-forged XFF must NOT change the resolved IP (non-spoofable:
    the middleware only reads Fly-Client-IP, which the proxy sets/overwrites;
    XFF is never consulted)."""
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
