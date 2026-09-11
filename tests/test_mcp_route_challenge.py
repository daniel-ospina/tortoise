"""#2864 — RFC 9728 challenge on `/mcp` 401s + `/mcp` route canonicalization.

Two defects on the hosted MCP surface:

1. ``POST /mcp`` returned a 401 with **no** ``WWW-Authenticate`` header, so an MCP
   client had no discoverable path from the rejection to the authorization server.
   ``_jsonrpc_error`` had no ``headers`` parameter, so none of the
   ``TeamResolutionMiddleware`` 401 sites could emit a challenge.
2. ``/mcp`` 307'd to ``/mcp/`` (Starlette ``redirect_slashes``). The #985 chain
   makes that lossy: the Fly edge 301s http→https, and 301 is NOT
   method-preserving (RFC 9110 section 15.4.2), so a POST can arrive as a GET —
   hence the 405 reported in #985. (307 itself is method-preserving, section
   15.4.8; the lossy hop is the 301.)

The challenge must NOT be emitted by ``StaticKeyMiddleware`` (self-host): that
surface has no authorization server, so the header would point at a 404.

Invariants pinned here:
  * no method or path form on ``/mcp`` produces a 3xx
  * the challenge URL is absolute, root_path-free, and resolves to a PRM
    document whose ``resource`` is the canonical no-slash connector URL
  * the rewrite is EXACT-match — it can never widen the surface

LANE: docker lane. ``TestClient(hosted_app)`` runs ``hosted_api._lifespan``, which
composes the FastMCP session manager and pre-warms the graph backend. That needs a live
FalkorDB URI (hence ``slow_files:`` in ``config/ci-surfaces.yml`` and the ``test-slow``
legs); it is NOT in ``carve_out:`` (that set is embedded-by-design). Set
``TORTOISE_DB_URI`` for a local run. The module itself runs in a few seconds.
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
        # NOTE: `assert not body["resource"].endswith("/mcp/")` used to sit here.
        # It was unreachable — a string ending in "/mcp/" does not end in "/mcp",
        # so the line above already forbids it. The assertion that actually
        # carries weight is the endswith("/mcp") one; the failure message below
        # exists to explain WHY the no-slash form is required.
        assert "/mcp/" not in body["resource"], (
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

class TestChallengeAbsentWhereNoAuthorizationServer:
    """``TeamResolutionMiddleware`` is the auth middleware for TWO surfaces with
    no authorization server, and neither must ever see a challenge:

    * **tenant-mode self-host** — ``create_http_app``'s DEFAULT ``auth_mode``, i.e.
      ``tortoise serve --http``. It runs this same middleware against a registry
      that registers no ``/.well-known/*`` routes, so a challenge would 404. This
      was a real regression: emitting unconditionally put a
      ``resource_metadata`` pointer to a 404 on the default self-host surface.
    * ``StaticKeyMiddleware`` — single-key self-host (``--auth static``).

    Only ``hosted_api`` opts in via ``emit_oauth_challenge=True``.
    """

    def test_real_tenant_mode_app_emits_no_challenge(self):
        from tortoise.mcp_server import create_http_app

        app = create_http_app(allowed_origins=[], auth_mode="tenant")
        with TestClient(app, follow_redirects=False) as tc:
            r = tc.post("/", json={})
        assert r.status_code == 401
        assert "www-authenticate" not in {k.lower() for k in r.headers}, (
            "tenant-mode self-host has no AS — a challenge would point at a 404"
        )

    def test_the_hosted_app_still_does_emit_it(self, hosted_client):
        """The flip side — guard against 'fix it by disabling everywhere'."""
        assert _challenge_url(hosted_client.post("/mcp", json={})).endswith(PRM_PATH)

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


class TestChallengeHostInjection:
    """The challenge value is reflected into a response the client uses to steer
    OAuth discovery, and the app's own Host guard does NOT cover it: FastMCP's
    ``_normalize_host`` splits on the LAST colon, so
    ``Host: api.premiselabs.co:443@evil.com`` passes the allowlist while RFC 3986
    resolves the authority to ``evil.com``. Reflecting it would hand the attacker
    the client's authorization-code exchange. Verified exploitable before the
    validation was added.

    Status and header presence are asserted per case so the parameters cannot
    silently go vacuous. Both defenses FAIL CLOSED — they suppress the challenge
    rather than emit a sanitized one, which is the stronger outcome:

    * ``_SAFE_HOST_RE`` rejects the shape → the request is an ordinary 401 with
      **no** ``WWW-Authenticate`` at all.
    * FastMCP's HostOriginGuard rejects a non-allowlisted host → **421**, before
      this middleware runs.

    The positive control (a legitimate host still yields a well-formed
    challenge) is what stops this from being satisfiable by "never emit".
    """

    @pytest.mark.parametrize("bad_host,expected", [
        # `_SAFE_HOST_RE` rejects these (userinfo / newline) → fail-closed 401.
        ("api.premiselabs.co:443@evil.com", 401),
        ("api.premiselabs.co\n", 401),
        # The quote makes FastMCP's normalizer reject the host outright → 421.
        ('api.premiselabs.co",error="x@evil.com', 421),
        # Not allowlisted at all → 421 from the framework guard.
        ("evil.com", 421),
    ])
    def test_attacker_host_never_reaches_the_header(self, hosted_client, bad_host, expected):
        r = hosted_client.post("/mcp", json={}, headers={"Host": bad_host})
        assert r.status_code == expected, f"{bad_host!r} → {r.status_code}"
        header = r.headers.get("www-authenticate")
        assert header is None, (
            f"{bad_host!r} produced a challenge — it must fail closed: {header!r}"
        )
        assert not any("evil.com" in v for v in r.headers.values()), (
            f"{bad_host!r} was reflected into a response header"
        )

    def test_legitimate_host_still_yields_a_well_formed_challenge(self, hosted_client):
        """Positive control: the guards above must not be satisfiable by simply
        never emitting a challenge."""
        r = hosted_client.post("/mcp", json={}, headers={"Host": "api.premiselabs.co"})
        assert r.status_code == 401
        header = r.headers.get("www-authenticate")
        assert header is not None
        assert header.count('"') == 2, header
        assert header.endswith(f'{PRM_PATH}"'), header

    def test_quoted_host_is_rejected_by_the_framework_guard(self, hosted_client):
        """A quote-bearing Host is not allowlisted, so FastMCP's Host/Origin guard
        answers 421 BEFORE the middleware runs. The regex-level property (a `"`
        cannot break out of the quoted header value) is pinned directly in
        ``TestResourceMetadataUrl.test_unsafe_or_absent_host_yields_no_url``.

        Asserting the status matters: the previous form
        (``header is None or header.count('"') == 2``) could NEVER fail — `"` is
        outside the regex, so the value is always None — and asserted nothing.
        """
        r = hosted_client.post("/mcp", json={}, headers={"Host": 'a"b.example'})
        assert r.status_code == 421, r.status_code
        assert r.headers.get("www-authenticate") is None
        assert not any("evil" in v or '"b.example' in v for v in r.headers.values())


# ── 4. Origin derivation unit ───────────────────────────────────────────────

class TestResourceMetadataUrl:
    """Unit-level pin on the origin derivation — the integration tests above run
    with an in-process ``http://testserver`` origin, so they cannot catch a
    scheme/host regression that only shows up behind the proxy."""

    @staticmethod
    def _url_for(scope_extra: dict, headers: dict | None = None) -> str | None:
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

    def test_port_from_the_host_header_is_preserved(self):
        url = self._url_for({}, {"host": "localhost:8000"})
        assert url == f"http://localhost:8000{PRM_PATH}", url

    def test_ipv6_literal_host_is_accepted(self):
        url = self._url_for({}, {"host": "[::1]:8000"})
        assert url == f"http://[::1]:8000{PRM_PATH}", url

    def test_full_and_mapped_ipv6_literals_are_accepted(self):
        for host in ("[1:2:3:4:5:6:7:8]", "[::ffff:1.2.3.4]", "[2001:db8::1]"):
            assert self._url_for({}, {"host": host}) == (
                f"http://{host}{PRM_PATH}"), host

    def test_port_at_the_usable_boundary_is_kept(self):
        assert self._url_for({}, {"host": "h.example:65535"}) == (
            f"http://h.example:65535{PRM_PATH}"), "65535 is the last usable port"

    def test_defaults_to_https_when_scheme_unset(self):
        assert self._url_for({"scheme": ""}, {"host": "h.example"}).startswith("https://")

    @pytest.mark.parametrize("host", [
        None,                                 # absent
        "",                                   # empty
        "api.premiselabs.co:443@evil.com",     # userinfo — FastMCP's guard normalizes to the
                                               # allowlisted host, RFC 3986 resolves evil.com
        'a"b.example',                        # breaks out of the quoted header value
        "a,b.example",
        "a b.example",
        "a%2fb.example",
        "a/b.example",
        'a\\b.example',
        "-bad.example",
        "api.premiselabs.co\n",     # `$` would accept this; `\Z` does not
        "api.premiselabs.co\r\n",
        "[127.0.0.1]",               # bracketed IPv4 is not a legal RFC 3986 host
        "[1.2.3.4]:80",
        "[:]",                       # regex-shaped but not a valid IPv6 literal
        "[:::]",
        "[1:2:3]",
        "[1::2::3]",
        "[12345::1]",
        "api.premiselabs.co:99999",  # parses here, but not a usable URL port
        "api.premiselabs.co:65536",
        "api.premiselabs.co:00000",
        "x" * 256,
    ])
    def test_unsafe_or_absent_host_yields_no_url(self, host):
        """Fail-closed on SYNTACTICALLY unsafe input: an unparseable or
        header-breaking Host produces NO challenge rather than reflecting the
        value. This was verified exploitable before the check existed.

        Note this is NOT the allowlist check. A syntactically valid but
        non-allowlisted host (``evil.com``) still yields a URL here — enforcement
        of the allowlist is FastMCP's HostOriginGuard (421), verified end-to-end
        in ``TestChallengeHostInjection``. Keeping the two concerns separate is
        deliberate: this function must not silently depend on the guard having
        run first."""
        headers = {} if host is None else {"host": host}
        assert self._url_for({}, headers) is None, host


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

    @pytest.mark.parametrize("path", [
        "/mcp/../other", "/mcp/..;/other", "//mcp", "/mcp//",
        "/mcp%2f", "/mcp%2F", "/Mcp", "/mcpfoo", "/mcp/x",
        "/mcp/teams/t1", "/MCP",
    ])
    def test_bypass_family_is_not_rewritten(self, path):
        """Traversal / shadowing family, driven through a RAW ASGI scope.

        ``httpx`` normalizes dot-segments BEFORE the ASGI call, so a
        ``TestClient`` probe for ``/mcp/../`` never reaches this middleware with
        that path — it tests a different request. Only a raw scope exercises the
        real thing. The rewrite is exact-match and can only ever narrow ``/mcp``
        onto the same mount, never open a sibling route."""
        import asyncio
        seen = asyncio.run(self._run(path, ""))
        assert seen["path"] == path, f"{path} was rewritten to {seen['path']}"

    @pytest.mark.parametrize("path", [
        "/mcp/../other", "//mcp", "/mcp%2f", "/Mcp", "/mcpfoo", "/mcp//",
    ])
    def test_bypass_family_is_not_rewritten_under_a_prefix(self, path):
        import asyncio
        seen = asyncio.run(self._run(path, "/x"))
        assert seen["path"] == path, f"{path} was rewritten to {seen['path']}"


def url_is_clean(url: str) -> bool:
    """`…/.well-known/oauth-protected-resource/mcp` exactly once, no root_path."""
    return url.endswith(PRM_PATH) and "/mcp/" not in url.split("/.well-known")[0]
