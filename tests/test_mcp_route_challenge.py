"""#2864 — RFC 9728 challenge on `/mcp` 401s + `/mcp` route canonicalization.

Two defects on the hosted MCP surface:

1. ``POST /mcp`` returned a 401 with **no** ``WWW-Authenticate`` header, so an MCP
   client had no discoverable path from the rejection to the authorization server.
   ``_jsonrpc_error`` had no ``headers`` parameter, so none of the
   ``TeamResolutionMiddleware`` 401 sites could emit a challenge.
2. ``/mcp`` 307'd to ``/mcp/`` (Starlette ``redirect_slashes``). A 307 on a
   JSON-RPC POST is a correctness hazard: POST-following HTTP stacks convert the
   method to GET per RFC 9110, and the #985 chain (the Fly edge 301s http→https)
   makes the round trip doubly lossy.

The challenge must NOT be emitted by ``StaticKeyMiddleware`` (self-host): that
surface has no authorization server, so the header would point at a 404.

Invariants pinned here:
  * no method or path form on ``/mcp`` produces a 3xx
  * the challenge URL is absolute, root_path-free, and resolves to a PRM
    document whose ``resource`` is the canonical no-slash connector URL
  * the rewrite is EXACT-match — it can never widen the surface

LANE: docker lane. ``TestClient(hosted_app)`` runs ``hosted_api._lifespan``, which
composes the FastMCP session manager and pre-warms the graph backend. The module runs
in ~100 s, so it is registered in ``config/ci-surfaces.yml`` under ``slow_files:`` and
runs in the ``test-slow`` legs; it is NOT in ``carve_out:`` (that set is
embedded-by-design). Set ``TORTOISE_DB_URI`` for a local run.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from tortoise.hosted_api import app as hosted_app

PRM_PATH = "/.well-known/oauth-protected-resource/mcp"


def _challenge_url(response) -> str:
    """Extract the resource_metadata value from a challenge header."""
    header = response.headers.get("www-authenticate")
    assert header, f"no WWW-Authenticate on {response.status_code}: {dict(response.headers)}"
    assert header.startswith('Bearer resource_metadata="'), header
    return header.split('resource_metadata="', 1)[1].rstrip('"')


@pytest.fixture
def hosted_client():
    """The REAL hosted app (not an isolated mount) — the defects live in the
    parent app's routing + the sub-app's auth middleware, so the isolated
    ``_mounted_test_client`` in test_mcp_http.py cannot observe them.

    Entering the context runs ``hosted_api._lifespan``, which composes the
    FastMCP session-manager lifespan (Starlette Mount does not do it for you).
    """
    with TestClient(hosted_app, follow_redirects=False) as client:
        yield client


# ── 1. No redirect on any method or path form ───────────────────────────────

class TestRouteCanonicalization:
    @pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
    @pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "DELETE"])
    def test_no_3xx_on_any_method_or_form(self, hosted_client, method, path):
        """The defect: POST /mcp → 307 → /mcp/ (and GET/HEAD too). After the
        fix the canonical connector URL is served directly."""
        kwargs = {"json": {}} if method in ("POST", "DELETE") else {}
        r = hosted_client.request(method, path, **kwargs)
        assert not (300 <= r.status_code < 400), (
            f"{method} {path} → {r.status_code} Location={r.headers.get('location')!r}"
        )
        assert "location" not in {k.lower() for k in r.headers}

    def test_both_forms_agree(self, hosted_client):
        """The two forms are the same resource — same status, same auth verdict."""
        bare = hosted_client.post("/mcp", json={})
        slashed = hosted_client.post("/mcp/", json={})
        assert bare.status_code == slashed.status_code == 401

    def test_get_metadata_served_on_both_forms(self, hosted_client):
        """GET /mcp is the documented base-protocol metadata route."""
        for path in ("/mcp", "/mcp/"):
            r = hosted_client.get(path, headers={"Accept": "application/json"})
            assert r.status_code == 200, path
            assert r.json()["protocol"] == "mcp", path

    def test_sse_accept_is_405_on_both_forms(self, hosted_client):
        """Bytes-Accept of text/event-stream gets 405 by design (epic #529 T8:
        a JSON body there breaks the MCP TS SDK's JSON-RPC parse). Pinned
        against tests/test_mcp_http.py:362-372 so the two lanes cannot drift."""
        for path in ("/mcp", "/mcp/"):
            r = hosted_client.get(path, headers={"Accept": "text/event-stream"})
            assert r.status_code == 405, path

    @pytest.mark.parametrize("path", ["/mcpfoo", "/Mcp", "/mcpXYZ"])
    def test_rewrite_is_exact_match_never_a_prefix(self, hosted_client, path):
        """A ``startswith("/mcp")`` implementation would rewrite these and
        shadow sibling routes. The rewrite must be EXACT-match only."""
        r = hosted_client.post(path, json={})
        assert r.status_code == 404, f"{path} → {r.status_code} (rewritten?)"
        assert not (300 <= r.status_code < 400), path


# ── 2. RFC 9728 challenge on the auth 401s ──────────────────────────────────

class TestAuthChallenge:
    def test_unauthenticated_post_carries_challenge(self, hosted_client):
        r = hosted_client.post("/mcp", json={})
        assert r.status_code == 401
        url = _challenge_url(r)
        assert url.endswith(PRM_PATH), url

    def test_challenge_present_on_both_path_forms(self, hosted_client):
        """The challenge must not depend on which form the client used."""
        for path in ("/mcp", "/mcp/"):
            url = _challenge_url(hosted_client.post(path, json={}))
            assert url.endswith(PRM_PATH), path

    def test_malformed_bearer_carries_challenge(self, hosted_client):
        """A wrong-shaped Bearer token is still an unauthenticated caller who
        could instead use the OAuth flow — the client must be able to discover
        the AS from this 401 too."""
        r = hosted_client.post("/mcp", json={}, headers={"Authorization": "Bearer not-a-known-prefix"})
        assert r.status_code == 401
        assert _challenge_url(r).endswith(PRM_PATH)

    def test_scheme_only_bearer_carries_challenge(self, hosted_client):
        """`Bearer` with an empty token hits its own 401 site."""
        r = hosted_client.post("/mcp", json={}, headers={"Authorization": "Bearer "})
        assert r.status_code == 401
        assert _challenge_url(r).endswith(PRM_PATH)

    def test_challenge_url_is_root_path_free(self, hosted_client):
        """Regression guard: this middleware runs INSIDE the sub-app mounted at
        /mcp, where ``request.base_url`` carries ``root_path="/mcp"``. Building
        from base_url yields ``…/mcp/.well-known/…`` — a 404. The origin must be
        derived from scheme + Host instead."""
        url = _challenge_url(hosted_client.post("/mcp", json={}))
        assert "/mcp/.well-known" not in url, url
        assert url.count("/mcp") == 1, url  # only the PRM path suffix
        assert url.split("://", 1)[1].startswith("testserver/"), url

    def test_challenge_target_resolves_and_is_self_consistent(self, hosted_client):
        """The whole point of the header: a client follows it and lands on a PRM
        document. Assert it 200s AND that its ``resource`` is the canonical
        no-slash connector URL — a challenge pointing at a 404, or at a PRM
        advertising a different resource, is worse than no challenge."""
        url = _challenge_url(hosted_client.post("/mcp", json={}))
        path = url.split("testserver", 1)[1]
        prm = hosted_client.get(path)
        assert prm.status_code == 200, prm.text
        body = prm.json()
        assert body["resource"].endswith("/mcp"), body["resource"]
        assert not body["resource"].endswith("/mcp/"), (
            f"PRM resource is slashed ({body['resource']}) but the challenge "
            f"advertises the no-slash form — the client would send the wrong aud"
        )
        assert body["authorization_servers"], body

    def test_challenge_present_under_a_proxy_shaped_host(self, hosted_client):
        """The header must reflect the CLIENT-VISIBLE host, not the internal
        service address, or the client follows it to the wrong origin."""
        r = hosted_client.post("/mcp", json={}, headers={"Host": "api.premiselabs.co"})
        url = _challenge_url(r)
        assert url.startswith("http://api.premiselabs.co/"), url

    def test_challenge_has_no_trailing_slash_before_the_doc(self, hosted_client):
        """RFC 9728 path-suffixed form: ``/.well-known/oauth-protected-resource/mcp``.
        A trailing slash on the resource would 404 against the registered routes."""
        url = _challenge_url(hosted_client.post("/mcp", json={}))
        assert "/.well-known/oauth-protected-resource//mcp" not in url
        assert not url.endswith("/mcp/"), url


# ── 3. Challenge absence where there is no authorization server ─────────────

class TestChallengeAbsentOnSelfHost:
    """``StaticKeyMiddleware`` (self-host, ``TORTOISE_AUTH_MODE=static``) has NO
    authorization server behind it. Emitting a challenge there sends the client
    to a 404 — worse than the honest bare 401."""

    @staticmethod
    def _static_app(api_key: str):
        from starlette.applications import Starlette
        from starlette.responses import PlainTextResponse
        from starlette.routing import Route

        from tortoise.mcp_auth import StaticKeyMiddleware

        inner = Starlette(routes=[Route("/", lambda _r: PlainTextResponse("ok"), methods=["POST"])])
        inner.add_middleware(StaticKeyMiddleware, api_key=api_key)
        return inner

    @staticmethod
    def _no_as_routes(app) -> list[str]:
        return [r.path for r in app.routes if "well-known" in getattr(r, "path", "")]

    def test_missing_bearer_401_has_no_challenge(self):
        app = self._static_app("tt_static_key")
        assert self._no_as_routes(app) == [], "precondition: no PRM route on this app"
        with TestClient(app, follow_redirects=False) as tc:
            r = tc.post("/", json={})
        assert r.status_code == 401
        assert "www-authenticate" not in {k.lower() for k in r.headers}, (
            "self-host static-key 401 must NOT carry a challenge — there is no AS"
        )

    def test_wrong_key_401_has_no_challenge(self):
        app = self._static_app("tt_static_key")
        with TestClient(app, follow_redirects=False) as tc:
            r = tc.post("/", json={}, headers={"Authorization": "Bearer tt_wrong"})
        assert r.status_code == 401
        assert "www-authenticate" not in {k.lower() for k in r.headers}


# ── 4. Origin derivation unit ───────────────────────────────────────────────

class TestResourceMetadataUrl:
    """Unit-level pin on the origin derivation — the integration tests above run
    with an in-process ``http://testserver`` origin, so they cannot catch a
    scheme/host regression that only shows up behind the proxy."""

    @staticmethod
    def _url_for(scope_extra: dict, headers: dict | None = None) -> str:
        from starlette.requests import Request

        from tortoise.mcp_auth import _resource_metadata_url

        scope = {
            "type": "http", "method": "POST", "path": "/", "raw_path": b"/",
            "scheme": "http", "server": ("testserver", 80),
            "root_path": "/mcp", "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }
        scope.update(scope_extra)
        return _resource_metadata_url(Request(scope))

    def test_uses_host_header_not_root_path(self):
        url = self._url_for({}, {"host": "api.premiselabs.co"})
        assert url == f"http://api.premiselabs.co{PRM_PATH}", url

    def test_root_path_is_stripped(self):
        """The regression this exists for: base_url would include root_path."""
        assert "/mcp/.well-known" not in self._url_for({}, {"host": "h.example"})
        assert url_is_clean(self._url_for({}, {"host": "h.example"}))

    def test_scheme_flows_from_forwarded_proto_scope(self):
        """``ForwardedProtoMiddleware`` (#985) rewrites scope["scheme"]; the
        challenge must respect it so the client is not sent to http."""
        assert self._url_for({"scheme": "https"}, {"host": "api.premiselabs.co"}) == (
            f"https://api.premiselabs.co{PRM_PATH}")

    def test_falls_back_to_scope_server_when_host_absent(self):
        """No Host header (non-HTTP/1.1 or a synthetic scope): fall back to the
        ASGI server address, omitting the port when it is the scheme default."""
        url = self._url_for({}, {})
        assert url == f"http://testserver{PRM_PATH}", url

    def test_non_default_port_is_kept(self):
        url = self._url_for({"server": ("localhost", 8000)}, {})
        assert url == f"http://localhost:8000{PRM_PATH}", url

    def test_https_default_port_is_omitted(self):
        url = self._url_for({"scheme": "https", "server": ("h.example", 443)}, {})
        assert url == f"https://h.example{PRM_PATH}", url

    def test_defaults_to_https_when_scheme_unset(self):
        scope = {"scheme": ""}
        assert self._url_for(scope, {"host": "h.example"}).startswith("https://")


class TestCanonicalizerRootPath:
    """The rewrite must be root_path-aware: under an ASGI mount prefix
    (``uvicorn --root-path /x``) ``scope["path"]`` is ``/x/mcp`` while
    Starlette's own route path is ``/mcp``, so a raw-path comparison silently
    re-enables the 307. Latent today (no root_path is configured anywhere in
    this repo) but a silent degradation, so it is pinned."""

    @staticmethod
    async def _run(path: str, root_path: str) -> dict:
        from tortoise.hosted_api import McpPathCanonicalizerMiddleware

        seen: dict = {}

        async def downstream(scope, receive, send):
            seen.update(scope)

        scope = {"type": "http", "method": "POST", "path": path,
                 "raw_path": path.encode(), "root_path": root_path,
                 "headers": [], "query_string": b""}
        await McpPathCanonicalizerMiddleware(downstream)(scope, b"", None)
        return seen

    def test_rewrites_with_no_root_path(self):
        import asyncio
        seen = asyncio.run(self._run("/mcp", ""))
        assert seen["path"] == "/mcp/"
        assert seen["raw_path"] == b"/mcp/"

    def test_rewrites_under_a_mount_prefix(self):
        import asyncio
        seen = asyncio.run(self._run("/x/mcp", "/x"))
        assert seen["path"] == "/x/mcp/", seen["path"]
        assert seen["raw_path"] == b"/x/mcp/", seen["raw_path"]

    def test_rewrites_the_unprefixed_form_under_a_mount_prefix(self):
        """Starlette routes on the root-stripped route path, so the UNPREFIXED
        ``/mcp`` must also be caught when a root_path is set — a raw-path
        comparison anchors on the prefix and misses this, silently restoring
        the 307 for the canonical connector URL."""
        import asyncio
        seen = asyncio.run(self._run("/mcp", "/x"))
        assert seen["path"] == "/x/mcp/", seen["path"]

    def test_does_not_rewrite_a_different_roots_mcp(self):
        """Exact-match guard (independent of root_path awareness): a path that
        merely ENDS in `/mcp` under a different root must not be rewritten."""
        import asyncio
        seen = asyncio.run(self._run("/x/mcp", ""))
        assert seen["path"] == "/x/mcp", "rewrite fired on a non-/mcp route"


def url_is_clean(url: str) -> bool:
    """`…/.well-known/oauth-protected-resource/mcp` exactly once, no root_path."""
    return url.endswith(PRM_PATH) and "/mcp/" not in url.split("/.well-known")[0]
